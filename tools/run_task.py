#!/usr/bin/env python3
"""Faehrt eine Task von der Lieferung bis zum Human Gate — mit Zustand und Grenzen.

    python3 devos/tools/run_task.py --task work/tasks/TASK-001-....md \
        --onto main --tests "python3 -m unittest discover"

Der Ablauf, und zwar vollstaendig, nicht als Absichtserklaerung:

    Task laden -> Builder baut -> Maschinentests -> Review-Paket -> Reviewer
    prueft -> Gate rechnen -> ggf. Builder korrigiert -> Mensch entscheidet

Fuenf Regeln, die dieses Programm durchsetzt und nicht nur beschreibt:

  1. HOECHSTENS ZWEI KORREKTURRUNDEN. Eine Korrektur nach fehlgeschlagenen
     Maschinentests zaehlt genauso wie eine nach einem Reviewer-Befund. Danach
     entscheidet der Mensch, egal wie nah eine Loesung scheint.
  2. CONFLICT, fehlender Kontext, technische Blockade und erschoepftes Budget
     fuehren alle an dieselbe Stelle: das Human Gate. Kein Sonderweg.
  3. Zeit, Aufrufe und Kosten sind ausdruecklich begrenzt. Kein Wert ist
     unbegrenzt; was nicht messbar ist, bindet nicht und sagt das.
  4. Der Laufzustand liegt auf der Platte. Nach einem Absturz wird fortgesetzt,
     nicht blind wiederholt — besonders nicht der teure Builder-Aufruf.
  5. Jeder neue Commit braucht neue Tests und ein neues Review. Bewegt sich der
     Head, verfallen Paket und Urteil der Runde; sie werden neu erzeugt.

Und die Regel, die dieses Programm ausdruecklich NICHT bricht:

  `PASS` heisst *bereit fuer die menschliche Entscheidung*. Es gibt keinen
  automatischen Merge. Dieses Programm uebernimmt nichts, niemals.

Exit: 0 bereit fuer die Entscheidung (Gate PASS) · 1 Korrektur noetig und
      Budget erschoepft · 2 Human Gate aus einem anderen Grund · 3 ungueltiges
      Ergebnis · 5 technische Blockade
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from project_config import Config      # noqa: E402

GATE_NAMEN = {0: "MERGEABLE_PENDING_HUMAN", 1: "CHANGES_REQUIRED",
              2: "HUMAN_DECISION_REQUIRED", 3: "INVALID_RESULT"}


def jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(root: str, *args: str) -> tuple[int, str, str]:
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Lauf:
    """Der Laufzustand. Wird nach JEDEM Schritt geschrieben, nicht am Ende.

    Ein Zustand, der nur am Ende geschrieben wird, ist nach einem Absturz genau
    das, wovor er schuetzen sollte: nicht vorhanden.
    """

    def __init__(self, pfad: Path):
        self.pfad = pfad
        self.d: dict = {}

    def laden(self) -> bool:
        if not self.pfad.exists():
            return False
        try:
            self.d = json.loads(self.pfad.read_text(encoding="utf-8"))
            return isinstance(self.d, dict)
        except Exception:
            return False

    def speichern(self) -> None:
        self.d["updated_at"] = jetzt()
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.pfad.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.pfad)          # atomar: nie ein halb geschriebener Zustand

    def runde(self, n: int) -> dict:
        for r in self.d["rounds"]:
            if r["n"] == n:
                return r
        r = {"n": n, "head": None, "build": None, "package": None, "review": None, "gate": None}
        self.d["rounds"].append(r)
        return r

    def schritt_gilt(self, r: dict, name: str, head: str | None) -> bool:
        """Ein Schritt gilt, wenn er ok war UND an denselben Stand gebunden ist."""
        s = r.get(name)
        if not s or s.get("status") != "ok":
            return False
        if head is not None and s.get("head") != head:
            return False
        return all(Path(p).exists() for p in s.get("artifacts", []))

    def buchen(self, r: dict, name: str, **feld) -> None:
        r[name] = {"at": jetzt(), **feld}
        self.speichern()

    def verbraucht(self) -> dict:
        return self.d["spent"]


def budget_pruefen(lauf: Lauf, was: str) -> str | None:
    """Gibt den Grund zurueck, aus dem NICHT weitergemacht werden darf — oder None."""
    lim, sp = lauf.d["limits"], lauf.d["spent"]
    verstrichen = (time.time() - lauf.d["started_epoch"]) / 60
    sp["wall_minutes"] = round(verstrichen, 1)
    if lim.get("max_wall_minutes") and verstrichen > lim["max_wall_minutes"]:
        return (f"Zeitbudget erschoepft: {verstrichen:.0f} min > {lim['max_wall_minutes']} min")
    if was == "builder" and sp["builder_calls"] >= lim["max_builder_calls"]:
        return f"Builder-Aufrufe erschoepft: {sp['builder_calls']} von {lim['max_builder_calls']}"
    if was == "reviewer" and sp["reviewer_calls"] >= lim["max_reviewer_calls"]:
        return f"Reviewer-Aufrufe erschoepft: {sp['reviewer_calls']} von {lim['max_reviewer_calls']}"
    if lim.get("max_cost_usd") is not None and sp.get("cost_usd") is not None \
            and sp["cost_usd"] >= lim["max_cost_usd"]:
        return f"Kostenbudget erschoepft: {sp['cost_usd']} USD >= {lim['max_cost_usd']} USD"
    return None


def brief_schreiben(pfad: Path, lauf: Lauf, runde: int, anlass: str,
                    gate: dict | None, review: dict | None, testprotokoll: str | None) -> None:
    """Der Auftrag an den Builder — erzeugt, nicht von Hand gepflegt.

    Der Builder bekommt hier zurueck, was das Verfahren gefunden hat. Ohne
    diesen Ruecklauf ist der Reviewer ein Briefkasten und die Runde eine
    Behauptung.
    """
    t = lauf.d["task"]
    L = [f"# BUILD-BRIEF — {t['id']} · Runde {runde}", "",
         f"**Anlass:** {anlass}", f"**Erzeugt:** `{jetzt()}`",
         f"**Basis:** `{lauf.d['base']}`", "",
         "Du bist BUILDER. Du lieferst als **Commit** auf dem aktuellen Branch. Eine "
         "Aenderung, die nicht committet ist, ist keine Lieferung: das Verfahren bindet "
         "Tests und Urteil an eine Revision, und ein schmutziges Arbeitsverzeichnis "
         "gehoert zu keiner.", "",
         f"Es sind noch **{lauf.d['limits']['max_correction_rounds'] - lauf.d['spent']['corrections']} "
         "Korrekturrunde(n)** uebrig. Danach entscheidet der Mensch ueber den Stand, der dann "
         "vorliegt — auch wenn er unfertig ist.", "",
         "## Die Task im Wortlaut", "", "```markdown", t["raw"].rstrip(), "```", ""]

    if testprotokoll:
        L += ["## Die Maschinentests sind fehlgeschlagen", "",
              "Der Reviewer wurde **nicht** gerufen — ein fehlgeschlagener Testlauf ist kein "
              "Reviewgegenstand, sondern eine Reparatur.", "", "```",
              testprotokoll[-8000:].strip(), "```", ""]

    if gate:
        L += ["## Das Gate", "", f"- Ergebnis: **{gate.get('gate')}**",
              f"- vom Reviewer erklaert: `{gate.get('declared_status')}`", ""]
        for schl, titel in (("acceptance_not_met", "Acceptance nicht erfuellt"),
                            ("acceptance_uncovered", "Acceptance vom Reviewer nicht bewertet"),
                            ("acceptance_unbound", "bewertet, aber so nicht gefordert"),
                            ("acceptance_unverifiable", "Acceptance nicht pruefbar")):
            if gate.get(schl):
                L += [f"**{titel}:**", ""] + [f"- {x}" for x in gate[schl]] + [""]
        if gate.get("escalations"):
            L += ["**Was die Maschine festgestellt hat:**", ""] + \
                 [f"- {x}" for x in gate["escalations"]] + [""]

    if review:
        for schl, titel in (("blocking", "Blockierende Befunde"),
                            ("governance_conflicts", "Governance-Konflikte"),
                            ("unproven_claims", "Unbelegte Behauptungen"),
                            ("non_blocking", "Nicht blockierend")):
            eintraege = review.get(schl) or []
            if not eintraege:
                continue
            L += [f"## {titel}", ""]
            for e in eintraege:
                kopf = e.get("what") or e.get("claim") or e.get("id") or "—"
                L += [f"### {kopf}"]
                for k in ("where", "why", "class", "suggested_fix", "what_would_prove_it", "id"):
                    if e.get(k):
                        L += [f"- **{k}:** {e[k]}"]
                L += [""]
        if review.get("missing_context"):
            L += ["## Der Reviewer hat Kontext vermisst", "",
                  "Fehlt das wirklich, ergaenze es. Ist es vorhanden und der Reviewer hat es "
                  "nicht gefunden, ist das ein Befund am **Paket**, nicht am Code.", ""]
            L += [f"- {x}" for x in review["missing_context"]] + [""]

    L += ["## Was diese Runde NICHT tut", "",
          "- Den Umfang erweitern. Was nicht in der Task steht, gehoert nicht in diesen Commit.",
          "- Einen Test abschalten, ueberspringen oder aufweichen, damit er gruen wird.",
          "- Einen Befund wegdiskutieren, ohne ihn am Code zu pruefen. Ist ein Befund falsch, "
          "widerlege ihn im Commit-Text mit der Fundstelle.", ""]
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text("\n".join(L) + "\n", encoding="utf-8")


def builder_rufen(lauf: Lauf, cfg: Config, root: str, runde: int, brief: Path,
                  cmd: str, timeout: int) -> tuple[bool, str]:
    """Ruft den Builder. Gibt (erfolgreich, Begruendung) zurueck."""
    vorher = git(root, "rev-parse", "HEAD")[1]
    env = {**os.environ,
           "DEVOS_BRIEF": str(brief.resolve()),
           "DEVOS_TASK_FILE": str(Path(lauf.d["task"]["file"]).resolve()),
           "DEVOS_TASK_ID": lauf.d["task"]["id"],
           "DEVOS_ROUND": str(runde),
           "DEVOS_HEAD_BEFORE": vorher,
           "DEVOS_RUN_DIR": str(lauf.pfad.parent.resolve())}
    print(f"    Builder: {cmd}")
    try:
        p = subprocess.run(["bash", "-lc", cmd], cwd=root, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"Builder ueberschritt {timeout}s — abgebrochen, kein Ergebnis"
    lauf.d["spent"]["builder_calls"] += 1
    if p.returncode != 0:
        return False, f"Builder endete mit Exit {p.returncode}"
    nachher = git(root, "rev-parse", "HEAD")[1]
    if nachher == vorher:
        return False, ("der Builder hat keinen neuen Commit hinterlassen — es gibt nichts zu "
                       "pruefen. Eine Runde ohne Lieferung ist eine technische Blockade, "
                       "kein Ergebnis")
    schmutzig = git(root, "status", "--porcelain")[1]
    if schmutzig:
        return False, ("das Arbeitsverzeichnis ist nach dem Bau nicht sauber — die Lieferung "
                       "ist kein Commit und laesst sich an keine Revision binden:\n      "
                       + schmutzig[:400])
    return True, nachher


def main() -> int:
    ap = argparse.ArgumentParser(description="Eine Task bis zum Human Gate fahren")
    ap.add_argument("--task", required=True)
    ap.add_argument("--onto", default=None, help="Zielbranch fuer die merge-base")
    ap.add_argument("--base", default=None, help="feste Basis statt merge-base")
    ap.add_argument("--tests", default=None, help="Maschinentests, im isolierten Worktree")
    ap.add_argument("--root", default=".")
    ap.add_argument("--no-builder", action="store_true",
                    help="kein automatischer Builder-Aufruf; der Mensch oder ein Agent liefert "
                         "den Commit und startet erneut")
    ap.add_argument("--restart", action="store_true", help="vorhandenen Laufzustand verwerfen")
    ap.add_argument("--max-rounds", type=int, default=None, help="Korrekturrunden (Vorgabe 2)")
    ap.add_argument("--max-wall-minutes", type=int, default=None)
    ap.add_argument("--max-cost-usd", type=float, default=None)
    a = ap.parse_args()

    root = str(Path(a.root).resolve())
    cfg = Config(root)
    taskdatei = Path(a.task)
    if not taskdatei.exists():
        print(f"[5] Task nicht gefunden: {taskdatei}", file=sys.stderr)
        return 5
    rohtext = taskdatei.read_text(encoding="utf-8")
    task_id = rohtext.splitlines()[0].lstrip("# ").split()[0] if rohtext.strip() else taskdatei.stem

    lauf = Lauf(Path(root) / cfg.paths["runs"] / task_id / "run_state.json")
    frisch = a.restart or not lauf.laden()
    if a.restart and lauf.pfad.exists():
        lauf.pfad.unlink()
        lauf.d = {}

    if not frisch:
        if lauf.d.get("task", {}).get("sha256") != sha16(rohtext):
            print("[5] Die Taskdatei hat sich seit dem letzten Lauf geaendert.\n"
                  "    Ein laufendes Verfahren an einer veraenderten Task fortzusetzen wuerde\n"
                  "    Urteile an einen Gegenstand binden, den es nicht mehr gibt.\n"
                  "    Entweder die Task zuruecknehmen oder mit --restart neu beginnen.",
                  file=sys.stderr)
            return 5
        if lauf.d.get("state") in ("HUMAN_DECISION", "DONE"):
            print(f"Dieser Lauf steht bereits beim Menschen ({lauf.d.get('stop_reason')}).\n"
                  f"Entscheidungsblatt: {lauf.pfad.parent/'HANDOVER.md'}\n"
                  "Nach einer Korrektur: --restart, oder eine neue Task schneiden.")
            return {"PASS": 0}.get(lauf.d.get("outcome"), 2)
        print(f"Laufzustand gefunden: Runde {lauf.d['spent']['corrections'] + 1}, "
              f"{lauf.d['spent']['builder_calls']} Builder- und "
              f"{lauf.d['spent']['reviewer_calls']} Reviewer-Aufruf(e) verbraucht — "
              "es wird fortgesetzt, nicht wiederholt.")

    if frisch:
        rc, head, err = git(root, "rev-parse", "HEAD")
        if rc != 0:
            print(f"[5] kein git-Repo unter {root}: {err}", file=sys.stderr)
            return 5
        if a.base:
            base = git(root, "rev-parse", a.base)[1]
        elif a.onto:
            base = git(root, "merge-base", a.onto, "HEAD")[1]
        else:
            base = git(root, "rev-parse", "HEAD^")[1]
        if not base:
            print("[5] Basis nicht bestimmbar — --onto oder --base angeben.", file=sys.stderr)
            return 5
        limits = dict(cfg.limits)
        if a.max_rounds is not None:
            limits["max_correction_rounds"] = a.max_rounds
        if a.max_wall_minutes is not None:
            limits["max_wall_minutes"] = a.max_wall_minutes
        if a.max_cost_usd is not None:
            limits["max_cost_usd"] = a.max_cost_usd
        lauf.d = {
            "schema_version": "1.0", "project": cfg.project, "root": root,
            "task": {"id": task_id, "file": str(taskdatei), "sha256": sha16(rohtext), "raw": rohtext},
            "base": base, "base_ref": a.onto or a.base or "HEAD^",
            "tests_cmd": a.tests, "created_at": jetzt(), "started_epoch": time.time(),
            "limits": limits,
            "spent": {"corrections": 0, "builder_calls": 0, "reviewer_calls": 0,
                      "wall_minutes": 0.0, "cost_usd": None,
                      "prompt_tokens": 0, "completion_tokens": 0},
            "state": "BUILD", "rounds": [], "outcome": None, "stop_reason": None,
        }
        lauf.speichern()

    limits = lauf.d["limits"]
    runs_dir = lauf.pfad.parent
    builder_cmd = os.environ.get("DEVOS_BUILDER_CMD") or (cfg.builder.get("cmd") or "")
    builder_timeout = int(cfg.builder.get("timeout_seconds") or limits["builder_timeout_seconds"])

    print(f"\nDevOS · {lauf.d['task']['id']} · Projekt {cfg.project}")
    print(f"Basis {lauf.d['base'][:12]} · hoechstens {limits['max_correction_rounds']} "
          f"Korrekturrunde(n) · Zeitbudget {limits['max_wall_minutes']} min")
    if limits.get("max_cost_usd") is None:
        print("Kostenbudget: nicht gesetzt — Token werden gezaehlt, Kosten nur mit "
              "DEVOS_REVIEWER_PRICE_IN/OUT berechnet.")

    def anhalten(grund: str, zustand: str, code: int, outcome: str | None = None) -> int:
        lauf.d["state"] = zustand
        lauf.d["stop_reason"] = grund
        lauf.d["outcome"] = outcome
        lauf.speichern()
        uebergabe(lauf, runs_dir)
        print(f"\n{'=' * 62}")
        print(f"ERGEBNIS: {zustand} — {grund}")
        print(f"Entscheidungsblatt: {runs_dir / 'HANDOVER.md'}")
        print("Die Entscheidung liegt beim Menschen. Uebernommen wird von Hand.")
        return code

    anlass = "Erstlieferung"
    letztes_gate = letztes_review = None
    letztes_protokoll = None

    while True:
        runde_n = lauf.d["spent"]["corrections"] + 1
        r = lauf.runde(runde_n)
        rdir = runs_dir / f"round-{runde_n}"
        rdir.mkdir(parents=True, exist_ok=True)
        print(f"\n── Runde {runde_n} · {anlass} " + "─" * max(0, 40 - len(anlass)))

        # ---------------------------------------------------------------- 1 bauen
        if not lauf.schritt_gilt(r, "build", None):
            brief = rdir / "BUILD-BRIEF.md"
            brief_schreiben(brief, lauf, runde_n, anlass, letztes_gate, letztes_review,
                            letztes_protokoll)
            if a.no_builder or not builder_cmd:
                vorher = git(root, "rev-parse", "HEAD")[1]
                if runde_n == 1 and vorher != lauf.d["base"]:
                    lauf.buchen(r, "build", status="ok", head=vorher, artifacts=[str(brief)],
                                how="vorgefundener Commit (kein Builder-Aufruf)")
                    r["head"] = vorher
                    lauf.speichern()
                else:
                    grund = ("kein Builder konfiguriert (DEVOS_BUILDER_CMD oder .devos.json "
                             "builder.cmd) bzw. --no-builder gesetzt")
                    lauf.buchen(r, "build", status="wartet", head=None, artifacts=[], why=grund)
                    print(f"    Auftrag geschrieben: {brief}")
                    return anhalten(
                        f"{grund}. Der Auftrag fuer Runde {runde_n} liegt in {brief}. "
                        "Nach dem Commit erneut starten — der Lauf wird fortgesetzt.",
                        "HUMAN_DECISION", 2)
            else:
                sperre = budget_pruefen(lauf, "builder")
                if sperre:
                    return anhalten(sperre, "HUMAN_DECISION", 2)
                ok, was = builder_rufen(lauf, cfg, root, runde_n, brief, builder_cmd, builder_timeout)
                if not ok:
                    lauf.buchen(r, "build", status="blockiert", head=None,
                                artifacts=[str(brief)], why=was)
                    return anhalten(f"technische Blockade im Bau: {was}", "HUMAN_DECISION", 5)
                lauf.buchen(r, "build", status="ok", head=was, artifacts=[str(brief)],
                            how="Builder-Aufruf")
                r["head"] = was
                lauf.speichern()

        head = git(root, "rev-parse", "HEAD")[1]
        if r.get("head") and r["head"] != head:
            print(f"    Der Head hat sich seit dem Bau bewegt ({r['head'][:12]} -> {head[:12]}) — "
                  "Paket und Urteil dieser Runde verfallen.")
        r["head"] = head
        lauf.speichern()
        if head == lauf.d["base"]:
            return anhalten("es gibt keine Lieferung: der Head ist die Basis", "HUMAN_DECISION", 5)

        # ------------------------------------------- 2 Paket erzeugen (mit Maschinentests)
        out_rel = str((Path(cfg.paths["review"]) / f"{lauf.d['task']['id']}" /
                       f"round-{runde_n}").as_posix())
        out_abs = Path(root) / out_rel
        if not lauf.schritt_gilt(r, "package", head):
            cmd = [sys.executable, str(HERE / "review_request.py"),
                   "--task", str(taskdatei), "--root", root, "--out", out_rel,
                   "--base", lauf.d["base"], "--head", head]
            if lauf.d["tests_cmd"]:
                cmd += ["--tests", lauf.d["tests_cmd"]]
            print("    Paket erzeugen und Maschinentests fahren …")
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                lauf.buchen(r, "package", status="blockiert", head=head, artifacts=[],
                            why=p.stderr.strip()[:400])
                return anhalten(f"Paket nicht erzeugbar: {p.stderr.strip()[:300]}",
                                "HUMAN_DECISION", 5)
            lauf.buchen(r, "package", status="ok", head=head,
                        artifacts=[str(out_abs / "review_context.json"),
                                   str(out_abs / "REVIEW-REQUEST.md")])
        ctx = json.loads((out_abs / "review_context.json").read_text(encoding="utf-8"))

        # Fehlgeschlagene Maschinentests sind kein Reviewgegenstand. Der Reviewer
        # wird nicht gerufen — das spart einen Aufruf und trifft die Sache: ein
        # roter Test ist eine Reparatur, kein Urteil.
        t = ctx["tests"]
        if lauf.d["tests_cmd"] and not (t.get("ran") is True and t.get("exit_code") == 0):
            protokoll = ""
            logp = out_abs / "test-output.txt"
            if logp.exists():
                protokoll = logp.read_text(encoding="utf-8")
            print(f"    Maschinentests FEHLGESCHLAGEN (Exit {t.get('exit_code')}) — "
                  "der Reviewer wird nicht gerufen.")
            if lauf.d["spent"]["corrections"] >= limits["max_correction_rounds"]:
                return anhalten(
                    f"Maschinentests fehlgeschlagen (Exit {t.get('exit_code')}) und "
                    f"{limits['max_correction_rounds']} Korrekturrunden verbraucht",
                    "HUMAN_DECISION", 1)
            lauf.d["spent"]["corrections"] += 1
            lauf.speichern()
            anlass = f"Maschinentests fehlgeschlagen (Exit {t.get('exit_code')})"
            letztes_gate = letztes_review = None
            letztes_protokoll = protokoll
            continue

        # ----------------------------------------------------------- 3 Reviewer
        res_datei = out_abs / "review_result.json"
        prov_datei = out_abs / "review_dispatch.json"
        if not lauf.schritt_gilt(r, "review", head):
            sperre = budget_pruefen(lauf, "reviewer")
            if sperre:
                return anhalten(sperre, "HUMAN_DECISION", 2)
            print("    an den Reviewer geben …")
            p = subprocess.run([sys.executable, str(HERE / "review_dispatch.py"),
                                "--request", str(out_abs / "REVIEW-REQUEST.md"),
                                "--out", str(res_datei),
                                "--context", str(out_abs / "review_context.json"),
                                "--root", root], capture_output=True, text=True)
            lauf.d["spent"]["reviewer_calls"] += 1
            sys.stdout.write("      " + p.stdout.replace("\n", "\n      ").rstrip() + "\n")
            if p.returncode != 0:
                lauf.buchen(r, "review", status="blockiert", head=head, artifacts=[],
                            why=p.stderr.strip()[:500], exit=p.returncode)
                return anhalten(f"kein Urteil geholt (Exit {p.returncode}): "
                                f"{p.stderr.strip()[:300]}", "HUMAN_DECISION",
                                2 if p.returncode == 2 else 5)
            if prov_datei.exists():
                try:
                    pv = json.loads(prov_datei.read_text(encoding="utf-8"))
                    if pv.get("cost_usd") is not None:
                        lauf.d["spent"]["cost_usd"] = round(
                            (lauf.d["spent"].get("cost_usd") or 0) + pv["cost_usd"], 6)
                    u = pv.get("usage_total") or {}
                    for k in ("prompt_tokens", "completion_tokens"):
                        lauf.d["spent"][k] = (lauf.d["spent"].get(k) or 0) + (u.get(k) or 0)
                except Exception:
                    pass
            lauf.buchen(r, "review", status="ok", head=head,
                        artifacts=[str(res_datei), str(prov_datei)])

        # ------------------------------------------------------------- 4 Gate
        # Das Gate wird IMMER neu gerechnet, nie aus dem Zustand uebernommen:
        # es ist deterministisch und billig, und ein gespeichertes Urteil waere
        # genau die Art Zwischenspeicher, die still veraltet.
        print("    Gate rechnen …")
        p = subprocess.run([sys.executable, str(HERE / "review_result.py"), str(res_datei),
                            "--context", str(out_abs / "review_context.json"),
                            "--dispatch", str(prov_datei), "--json"],
                           capture_output=True, text=True)
        try:
            gate = json.loads(p.stdout)
        except json.JSONDecodeError:
            return anhalten(f"das Gate hat nicht geurteilt: {p.stderr.strip()[:300]}",
                            "HUMAN_DECISION", 5)
        lauf.buchen(r, "gate", status="ok", head=head, exit=p.returncode,
                    result=gate.get("gate"), artifacts=[])
        (rdir / "gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        print(f"      {gate.get('gate')}")

        review = json.loads(res_datei.read_text(encoding="utf-8"))
        letztes_gate, letztes_review, letztes_protokoll = gate, review, None

        if p.returncode == 0:
            return anhalten("der Reviewer hat nichts Blockierendes gefunden und alle "
                            "Voraussetzungen sind nachgewiesen", "HUMAN_DECISION", 0, "PASS")
        if p.returncode != 1:
            return anhalten(f"{gate.get('gate')} — das entscheidet kein Programm",
                            "HUMAN_DECISION", p.returncode, gate.get("gate"))

        # CHANGES_REQUIRED: noch eine Runde, wenn das Budget sie hergibt.
        if lauf.d["spent"]["corrections"] >= limits["max_correction_rounds"]:
            return anhalten(f"CHANGES_REQUIRED und {limits['max_correction_rounds']} "
                            "Korrekturrunden verbraucht — weiter entscheidet der Mensch",
                            "HUMAN_DECISION", 1, "CHANGES_REQUIRED")
        lauf.d["spent"]["corrections"] += 1
        lauf.speichern()
        anlass = f"Korrektur nach {gate.get('gate')}"


def uebergabe(lauf: Lauf, runs_dir: Path) -> None:
    """Das Entscheidungsblatt wird erzeugt, nicht gepflegt."""
    p = subprocess.run([sys.executable, str(HERE / "handover.py"),
                        "--state", str(lauf.pfad), "--out", str(runs_dir / "HANDOVER.md")],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(f"  (Entscheidungsblatt nicht erzeugt: {p.stderr.strip()[:200]})", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
