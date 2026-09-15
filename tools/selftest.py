#!/usr/bin/env python3
"""Eigentest des DevOS-Werkzeugs — und er bricht ab, wenn er nicht besteht.

Das ist keine Stilfrage. Assay-Befund F006: die sechs Rechner des Workshops
erzeugen ein Feld "bestanden": false und laufen danach normal weiter. Eine Probe
ohne Abbruchwirkung ist eine Anzeige, kein Gate.

Ab Runde 2 haelt jede Probe mit dem Praefix F01..F05/N02 einen Befund fest, den
der Adversarial Reviewer gefunden hat. Sie sind Regressionsproben, keine Deko.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT, METRICS, REQUEST = HERE / "review_result.py", HERE / "delivery_metrics.py", HERE / "review_request.py"
DISPATCH = HERE / "review_dispatch.py"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED.append(name) if ok else FAILED.append((name, detail)))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  — {detail}" if detail and not ok else ""))


def run_gate(result: dict, context: dict | None = None, ctx_path: str | None = None) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "r.json"
        rp.write_text(json.dumps(result), encoding="utf-8")
        cmd = [sys.executable, str(RESULT), str(rp), "--json"]
        if ctx_path is not None:
            cmd += ["--context", ctx_path]
        elif context is not None:
            cp = Path(td) / "c.json"
            cp.write_text(json.dumps(context), encoding="utf-8")
            cmd += ["--context", str(cp)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return p.returncode, json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.returncode, {"stdout": p.stdout, "stderr": p.stderr}


HEAD_A = "4cbc9864e197aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD_B = "4cbc9864e197bbbbbbbbbbbbbbbbbbbbbbbbbbbb"   # gleiches 12-Zeichen-Praefix
BASE_A = "1111111111111111111111111111111111111111"


def base_result(**over) -> dict:
    r = {"schema_version": "1.0", "task_id": "TASK-000",
         "reviewed_range": {"base": BASE_A, "head": HEAD_A},
         "status": "PASS", "context_sufficient": True, "blocking": [], "non_blocking": [],
         "reviewed": {"files": ["x.py"], "acceptance_items": [{"item": "A", "verdict": "met"}],
                      "tests": {"judged": "adequate"}}}
    r.update(over)
    return r


def ok_context(**over) -> dict:
    c = {"task": {"id": "TASK-000"}, "range": {"base": BASE_A, "head": HEAD_A},
         "tests": {"ran": True, "passed": True, "isolated": True}, "omitted": []}
    c.update(over)
    return c


def git(td: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", td, *args], capture_output=True, text=True)


GUELTIG = {
    "schema_version": "1.0", "task_id": "TASK-T",
    "reviewed_range": {"base": "b" * 40, "head": "h" * 40},
    "status": "CHANGES_REQUIRED", "context_sufficient": True,
    "blocking": [{"what": "x", "where": "a.py:1", "why": "y"}], "non_blocking": [],
    "reviewed": {"files": ["a.py"], "acceptance_items": [{"item": "A", "verdict": "met"}],
                 "tests": {"judged": "adequate"}},
}
SCHLUESSEL = "sk-geheim-darf-nirgends-auftauchen"


def mock_server(antworten: list[str], status: int = 200):
    """Lokaler OpenAI-kompatibler Endpunkt. Gibt (base_url, stop, gesehen) zurueck."""
    import http.server, threading
    gesehen = []
    folge = list(antworten)

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            gesehen.append({"auth": self.headers.get("Authorization", ""),
                            "body": json.loads(self.rfile.read(n).decode())})
            if status != 200:
                self.send_response(status); self.end_headers(); self.wfile.write(b"kaputt"); return
            inhalt = folge.pop(0) if folge else "{}"
            payload = json.dumps({"choices": [{"message": {"content": inhalt}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/v1", srv.shutdown, gesehen


def dispatch(td: str, antworten: list[str], status: int = 200, env_extra: dict | None = None,
             dry: bool = False) -> tuple[int, str, str]:
    base, stop, gesehen = mock_server(antworten, status)
    try:
        req = Path(td) / "REVIEW-REQUEST.md"
        req.write_text("# REVIEW REQUEST — TASK-T\n\nPaketinhalt.\n", encoding="utf-8")
        env = {**os.environ, "DEVOS_REVIEWER_API_KEY": SCHLUESSEL,
               "DEVOS_REVIEWER_MODEL": "mock-modell", "DEVOS_REVIEWER_BASE_URL": base,
               "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
        env.update(env_extra or {})
        for k, v in list(env.items()):
            if v is None:
                env.pop(k)
        cmd = [sys.executable, str(DISPATCH), "--request", str(req),
               "--out", str(Path(td) / "review_result.json")]
        if dry:
            cmd.append("--dry-run")
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr
    finally:
        stop()


def transport_proben() -> None:
    with tempfile.TemporaryDirectory() as td:
        code, out, err = dispatch(td, [json.dumps(GUELTIG)])
        res = Path(td) / "review_result.json"
        prov = Path(td) / "review_dispatch.json"
        check("N01 gueltige Antwort => Exit 0 und review_result.json geschrieben",
              code == 0 and res.exists(), f"Exit {code} {err.strip()[:160]}")
        check("N01 Provenienz haelt Modell und Endpunkt fest",
              prov.exists() and json.loads(prov.read_text())["model"] == "mock-modell")
        geschrieben = "".join(f.read_text(encoding="utf-8") for f in Path(td).glob("*"))
        check("N01 der Schluessel taucht in KEINER geschriebenen Datei auf",
              SCHLUESSEL not in geschrieben and SCHLUESSEL not in out and SCHLUESSEL not in err)

    with tempfile.TemporaryDirectory() as td:
        code, _, _ = dispatch(td, ["kein json, nur gerede", json.dumps(GUELTIG)])
        prov = json.loads((Path(td) / "review_dispatch.json").read_text())
        check("N01 eine Reparaturrunde bei ungueltiger Antwort, dann Erfolg",
              code == 0 and len(prov["attempts"]) == 2, f"Exit {code}")
        check("N01 die verworfene Rohantwort bleibt als Beleg liegen",
              (Path(td) / "review_result_raw_r1.txt").exists())

    with tempfile.TemporaryDirectory() as td:
        code, _, _ = dispatch(td, ["murks", "wieder murks"])
        check("N01 zweimal ungueltig => Exit 4 und KEIN review_result.json",
              code == 4 and not (Path(td) / "review_result.json").exists(), f"Exit {code}")

    with tempfile.TemporaryDirectory() as td:
        bloed = dict(GUELTIG); bloed["status"] = "SIEHT_GUT_AUS"
        code, _, _ = dispatch(td, [json.dumps(bloed), json.dumps(bloed)])
        check("N01 schemawidriges Urteil wird nicht durchgereicht", code == 4, f"Exit {code}")

    with tempfile.TemporaryDirectory() as td:
        code, _, err = dispatch(td, [json.dumps(GUELTIG)], env_extra={"DEVOS_REVIEWER_API_KEY": None})
        check("N01 fehlender Schluessel => Exit 2, nicht stiller Fehlschlag",
              code == 2 and "DEVOS_REVIEWER_API_KEY" in err, f"Exit {code}")

    with tempfile.TemporaryDirectory() as td:
        code, _, err = dispatch(td, [], status=500)
        check("N01 Endpunktfehler => Exit 5 mit Klartext", code == 5 and "HTTP 500" in err,
              f"Exit {code} {err.strip()[:120]}")

    with tempfile.TemporaryDirectory() as td:
        code, out, _ = dispatch(td, [json.dumps(GUELTIG)], dry=True)
        check("N01 dry-run schreibt die Anfrage und sendet nichts",
              code == 0 and (Path(td) / "review_request_payload.json").exists()
              and not (Path(td) / "review_dispatch.json").exists(), f"Exit {code}")


def main() -> int:
    print("DevOS Eigentest\n")

    print("Gate — Grundregeln:")
    code, out = run_gate(base_result(), ok_context())
    check("sauberes PASS mit isoliert bestandenen Tests => MERGEABLE_PENDING_HUMAN, Exit 0",
          code == 0 and out.get("gate", "").startswith("MERGEABLE"), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(blocking=[{"what": "Luecke", "where": "a.py:4", "why": "kein Pfad"}]), ok_context())
    check("PASS mit blockierendem Befund => CHANGES_REQUIRED, Exit 1", code == 1, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(context_sufficient=False, missing_context=["Diff fehlte"]), ok_context())
    check("context_sufficient=false => INSUFFICIENT_CONTEXT, Exit 2",
          code == 2 and "INSUFFICIENT_CONTEXT" in out.get("gate", ""), f"Exit {code} {out.get('gate')}")

    print("\nF01 — Kontextbindung und Identitaet (Reviewer-Befund Runde 1):")
    code, out = run_gate(base_result())
    check("F01 ohne --context ist PASS unerreichbar", code != 0, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ctx_path="/gibt/es/nicht.json")
    check("F01 --context zeigt ins Leere => nicht Exit 0", code != 0, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(task_id="ANDERE-TASK"), ok_context())
    check("F01 task_id weicht vom Paket ab => INSUFFICIENT_CONTEXT",
          code == 2 and "INSUFFICIENT_CONTEXT" in out.get("gate", ""), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(reviewed_range={"base": BASE_A, "head": HEAD_B}), ok_context())
    check("F01 zwei SHAs mit gleichem 12-Zeichen-Praefix sind NICHT gleich",
          code == 2, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(reviewed_range={"base": BASE_A, "head": ""}), ok_context())
    check("F01 leerer head => INSUFFICIENT_CONTEXT", code == 2, f"Exit {code} {out.get('gate')}")

    print("\nF02 — Schema und Acceptance als Gate-Bedingungen:")
    r = base_result(); r["reviewed"]["tests"]["judged"] = "banana"
    code, _ = run_gate(r, ok_context())
    check("F02 ungueltiger Enumwert => INVALID_RESULT, Exit 3", code == 3, f"Exit {code}")
    code, _ = run_gate(base_result(blocking={}), ok_context())
    check("F02 blocking als Objekt statt Liste => INVALID_RESULT", code == 3, f"Exit {code}")
    code, _ = run_gate(base_result(unbekanntes_feld=1), ok_context())
    check("F02 unbekanntes Feld => INVALID_RESULT (additionalProperties)", code == 3, f"Exit {code}")
    r = base_result(); r["reviewed"]["acceptance_items"] = [{"item": "A", "verdict": "not_met", "why": "nein"}]
    code, out = run_gate(r, ok_context())
    check("F02 PASS mit Acceptance not_met => CHANGES_REQUIRED", code == 1, f"Exit {code} {out.get('gate')}")
    r = base_result(); r["reviewed"]["acceptance_items"] = [{"item": "A", "verdict": "unverifiable"}]
    code, out = run_gate(r, ok_context())
    check("F02 Acceptance unverifiable => INSUFFICIENT_CONTEXT", code == 2, f"Exit {code} {out.get('gate')}")

    print("\nF03 — Tests gehoeren zum geprueften Stand:")
    code, out = run_gate(base_result(), ok_context(tests={"ran": True, "passed": True, "isolated": False}))
    check("F03 nicht isolierter Testlauf => PASS ausgeschlossen", code == 1, f"Exit {code} {out.get('gate')}")
    code, _ = run_gate(base_result(), ok_context(tests={"ran": True, "passed": False, "isolated": True}))
    check("F03 fehlgeschlagene Tests => CHANGES_REQUIRED", code == 1, f"Exit {code}")
    code, _ = run_gate(base_result(), ok_context(tests={"ran": False}))
    check("F03 keine Tests gelaufen => CHANGES_REQUIRED", code == 1, f"Exit {code}")

    print("\nN02 — Governance-Konflikt schlaegt jeden Ausgangsstatus:")
    code, out = run_gate(base_result(status="CHANGES_REQUIRED",
                                     blocking=[{"what": "x", "where": "y", "why": "z"}],
                                     governance_conflicts=[{"id": "D-144", "what": "Rechte erweitert"}]),
                         ok_context())
    check("N02 CHANGES_REQUIRED + Governance-Konflikt => CONFLICT",
          "CONFLICT" in out.get("gate", "") and code == 2, f"Exit {code} {out.get('gate')}")

    print("\nF03/F04 — Paketerzeugung gegen ein echtes Repo:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "alt.py").write_text("print(1)\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        git(td, "mv", "alt.py", "neu.py")
        (Path(td) / "gross.txt").write_text("x\n" * 400, encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        t = Path(td) / "TASK-T.md"
        t.write_text("# TASK-T — Probe\n\n## Acceptance\n- etwas gilt\n- ein Punkt, der ueber\n"
                     "  zwei Zeilen laeuft\n\n## Forbidden\n- etwas nicht\n\n"
                     "## Relevante Beschluesse\n- D-144\n", encoding="utf-8")
        # Projektbindung: eigenes Register, eigener Praefix - das Werkzeug kennt
        # keine mahoraga-Pfade mehr.
        (Path(td) / "reg.md").write_text("## XY-007 — Probenorm\n\nDer Wortlaut.\n\n"
                                         "## XY-008 — danach\n", encoding="utf-8")
        # Datei NUR im schmutzigen Arbeitsverzeichnis — im Worktree des Heads darf sie fehlen
        (Path(td) / "nur_lokal.txt").write_text("reparatur\n", encoding="utf-8")
        out_dir = Path(td) / "rev"
        p = subprocess.run([sys.executable, str(REQUEST), "--task", str(t), "--root", td,
                            "--head", "HEAD", "--base", "HEAD~1", "--out", str(out_dir),
                            "--tests", "test -f nur_lokal.txt"], capture_output=True, text=True)
        check("Paketerzeugung laeuft", p.returncode == 0, p.stderr.strip()[:200])
        cf = out_dir / "review_context.json"
        if cf.exists():
            c = json.loads(cf.read_text(encoding="utf-8"))
            md = (out_dir / "REVIEW-REQUEST.md").read_text(encoding="utf-8")
            check("F03 Tests laufen isoliert gegen den geprueften Head",
                  c["tests"].get("isolated") is True and c["tests"].get("ran_against") == c["range"]["head"],
                  json.dumps(c["tests"])[:160])
            check("F03 eine nur lokal vorhandene Datei kann den Test nicht gruen faerben",
                  c["tests"]["passed"] is False, f"passed={c['tests'].get('passed')}")
            check("F03 Schmutzzustand des Arbeitsverzeichnisses wird ausgewiesen",
                  c["tests"].get("worktree_dirty") is True)
            paths = [f["path"] for f in c["files_changed"]]
            check("F04 Umbenennung liefert echte Pfade statt 'alt => neu'",
                  "neu.py" in paths and not any("=>" in x for x in paths), str(paths))
            check("F04 Umbenennung nennt die Herkunft",
                  any(f.get("renamed_from") == "alt.py" for f in c["files_changed"]))
            check("F04 base ist ein voller SHA, kein 'HEAD~1'", len(c["range"]["base"]) == 40, c["range"]["base"])
            check("F04 vollstaendiges Testprotokoll liegt als Artefakt daneben",
                  (out_dir / "test-output.txt").exists() and "output_sha256" in c["tests"])
            check("'omitted' ist Pflichtfeld, auch leer", "omitted" in c)
            check("das eigene Ausgabeverzeichnis wird ausgeschnitten und genannt",
                  not any(x.startswith("rev/") for x in c["diff_included"]))
            check("Acceptance mehrzeilig ungekuerzt",
                  c["task"]["acceptance"][1] == "ein Punkt, der ueber zwei Zeilen laeuft",
                  repr(c["task"]["acceptance"][1:]))
            check("Register-IDs erkannt", "D-144" in c["referenced_ids"])
            check("ohne .devos.json wird das Fehlen der Projektbindung gemeldet, nicht verschwiegen",
                  any(o["path"] == ".devos.json" for o in c["omitted"]), json.dumps(c["omitted"])[:200])
            check("Projektname steht im Kontext", c.get("project") is not None)
            check("Abschnitt 'Nicht mitgeschickt' steht im Paket", "## Nicht mitgeschickt" in md)
            check("volle SHAs stehen im Markdown", c["range"]["head"] in md)
            check("INSUFFICIENT_CONTEXT wird dem Reviewer angeboten", "INSUFFICIENT_CONTEXT" in md)

    print("\nProjektbindung ueber .devos.json — das Werkzeug kennt kein Projekt:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / ".devos.json").write_text(json.dumps({
            "project": "probeprojekt", "id_pattern": r"\b(XY-\d{3})\b",
            "registers": {"XY": {"path": "reg.md", "kind": "heading",
                                 "heading_pattern": r"^#{1,4}\s+XY-\d{3}\b"}}}), encoding="utf-8")
        (Path(td) / "reg.md").write_text("## XY-007 — Probenorm\n\nDer Wortlaut steht hier.\n\n"
                                         "## XY-008 — danach\n", encoding="utf-8")
        (Path(td) / "a.py").write_text("x = 1\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        (Path(td) / "a.py").write_text("x = 2  # betrifft XY-007\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        tk = Path(td) / "T.md"; tk.write_text("# T-1 — Probe\n\n## Acceptance\n- gilt\n", encoding="utf-8")
        od = Path(td) / "rev"
        subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                        "--base", "HEAD~1", "--head", "HEAD", "--out", str(od)],
                       capture_output=True, text=True)
        c = json.loads((od / "review_context.json").read_text(encoding="utf-8"))
        check("Projektname aus .devos.json uebernommen", c.get("project") == "probeprojekt",
              str(c.get("project")))
        check("fremdes ID-Muster erkannt", "XY-007" in c["referenced_ids"], str(c["referenced_ids"]))
        check("Normquelle aus dem konfigurierten Register geholt",
              "XY-007" in c["norm_sources"] and "Der Wortlaut steht hier."
              in c["norm_sources"]["XY-007"]["text"], json.dumps(c["norm_sources"])[:160])
        check("Abschnitt endet an der naechsten gleichrangigen Ueberschrift",
              "XY-008" not in c["norm_sources"].get("XY-007", {}).get("text", ""))

    print("\nF05 — Prozessmessung misst, was sie behauptet:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "a.txt").write_text("x\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        b = git(td, "rev-parse", "HEAD").stdout.strip()
        (Path(td) / "b.txt").write_text("eine Zeile\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        h = git(td, "rev-parse", "HEAD").stdout.strip()
        log = Path(td) / "d.jsonl"
        log.write_text("\n".join(json.dumps(x) for x in [
            {"delivery": "T1", "event": "task_opened", "at": "2026-09-15T09:00:00Z", "base": b, "scope": "reduced"},
            {"delivery": "T1", "event": "human_minutes", "minutes": 30},
            {"delivery": "T1", "event": "builder_minutes", "minutes": 60},
            {"delivery": "T1", "event": "reviewer_minutes", "minutes": 10},
            {"delivery": "T1", "event": "suspended_artifact", "name": "K1-Rechnung"},
            {"delivery": "T1", "event": "review_round", "blocking": 2, "false_alarms": 1},
            {"delivery": "T1", "event": "accepted", "at": "2026-09-15T13:00:00Z", "commit": h},
            {"delivery": "T2", "event": "task_opened", "at": "2026-09-16T09:00:00Z"},
            {"delivery": "T3", "event": "task_opened", "at": "2026-09-17T09:00:00Z", "base": b},
            {"delivery": "T3", "event": "accepted", "at": "2026-09-17T10:00:00Z", "commit": "kaputteref"},
        ]), encoding="utf-8")
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(log), "--root", td, "--json"],
                           capture_output=True, text=True)
        js = json.loads(p.stdout)
        d1 = next(x for x in js["deliveries"] if x["delivery"] == "T1")
        check("Gesamtzeit summiert", d1["total_minutes"] == 100, str(d1["total_minutes"]))
        check("Menschenanteil berechnet", d1["human_share"] == 0.3, str(d1["human_share"]))
        check("Durchlaufzeit berechnet", d1["lead_time_hours"] == 4.0, str(d1["lead_time_hours"]))
        check("Treffergenauigkeit der Blocker berechnet", d1["blocker_precision"] == 0.5, str(d1["blocker_precision"]))
        check("F05 eine neue Datei mit einer Zeile ergibt 1 geaenderte Zeile, nicht 2",
              d1["changed_lines"] == 1, str(d1["changed_lines"]))
        check("F05 Dateizahl wird getrennt von der Zeilenzahl gefuehrt", d1["changed_files"] == 1,
              str(d1["changed_files"]))
        check("F05 nur eroeffnete Task zaehlt nicht als abgeschlossen", js["n_complete"] == 1,
              f"n_complete={js['n_complete']}")
        d3 = next(x for x in js["deliveries"] if x["delivery"] == "T3")
        check("F05 ungueltige git-Ref wird ausgewiesen, nicht zu 0",
              d3["git_error"] is not None and d3["changed_lines"] is None, str(d3.get("git_error"))[:120])
        check("F05 ausgesetzte Pflichtartefakte werden als offene Last gefuehrt",
              d1["suspended_artifacts"] == ["K1-Rechnung"])
        txt = subprocess.run([sys.executable, str(METRICS), "--log", str(log), "--root", td],
                             capture_output=True, text=True).stdout
        check("F05 kein Urteil ueber den Vollumfang aus einem reduzierten Pilot",
              "KEIN URTEIL" in txt and "WIDERLEGT" not in txt)
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(Path(td) / "weg.jsonl")],
                           capture_output=True, text=True)
        check("fehlendes Log ist ein Befund, kein stilles OK (Exit != 0)", p.returncode != 0, f"Exit {p.returncode}")

        kaputt = Path(td) / "kaputt.jsonl"
        kaputt.write_text('{"_comment":"zulaessig"}\n{"event":"task_opened"}\n', encoding="utf-8")
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(kaputt), "--root", td],
                           capture_output=True, text=True)
        check("kaputte Logzeile ist ein Befund, kein Absturz",
              p.returncode == 1 and "BEFUND" in p.stderr and "Traceback" not in p.stderr,
              f"Exit {p.returncode} {p.stderr.strip()[:120]}")

    print("\nN01 — Transport: der Mensch traegt das Paket nicht mehr:")
    transport_proben()

    print(f"\nbestanden: {len(PASSED)} von {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\nFEHLGESCHLAGEN:")
        for n, d in FAILED:
            print(f"  - {n}  {d}")
        print("\nverdict: FEHLGESCHLAGEN")
        return 1
    print("verdict: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
