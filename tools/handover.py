#!/usr/bin/env python3
"""Erzeugt das Entscheidungsblatt aus dem, was ohnehin da ist.

    python3 devos/tools/handover.py --state work/runs/TASK-001/run_state.json

Das ist der Gegenstand, ueber den der Mensch entscheidet, und zugleich der
Rumpf des Pull Requests. Es wird **erzeugt**, nie gepflegt: jede Zahl darin
stammt aus `run_state.json`, `review_context.json`, `review_result.json`,
`review_dispatch.json` oder aus git. Ein von Hand gefuehrtes Statusdokument
weicht ab, sobald jemand vergisst, es nachzuziehen — und dann entscheidet der
Mensch ueber einen Zustand, den es nicht gibt.

Zwei Dinge macht dieses Blatt ausdruecklich:

  * Es bindet die Entscheidung an **einen Commit**. Steht der Arbeitsstand
    woanders, sagt das Blatt das an erster Stelle, statt es dem Leser zu
    ueberlassen, den SHA zu vergleichen.
  * Es nennt, was der Reviewer NICHT gesehen hat. Ein Urteil ueber einen
    Ausschnitt ist ein Urteil ueber einen Ausschnitt.

`--pr-body` gibt nur den Rumpf aus, ohne Kopfzeile — zum Anlegen des PR.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SYMBOL = {"met": "erfuellt", "not_met": "NICHT erfuellt", "unverifiable": "nicht pruefbar"}


def lade(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def git(root: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def letzte_runde(st: dict) -> dict | None:
    fertige = [r for r in st.get("rounds", []) if (r.get("gate") or {}).get("status") == "ok"]
    return fertige[-1] if fertige else (st.get("rounds") or [None])[-1]


DATEI_SCHLUESSEL = {"review_context.json": "ctx", "review_result.json": "result",
                    "review_dispatch.json": "prov"}


def artefakte(runde: dict | None) -> dict:
    """Die Dateien der Runde — ueber die im Zustand gebuchten Pfade, nicht geraten.

    Geraten waere: den Ausgabeort neu berechnen. Dann findet dieses Blatt eine
    andere Datei als die, an der das Gate geurteilt hat, sobald jemand `--out`
    verschiebt.
    """
    out: dict = {}
    if not runde:
        return out
    for schritt in ("package", "review"):
        for pfad in (runde.get(schritt) or {}).get("artifacts", []):
            k = DATEI_SCHLUESSEL.get(Path(pfad).name)
            if k and Path(pfad).exists():
                out[k] = lade(Path(pfad))
    return out


def gate_der_runde(runs_dir: Path, runde: dict | None) -> dict | None:
    if not runde:
        return None
    rd = runs_dir / f"round-{runde['n']}" / "gate.json"
    return lade(rd) if rd.exists() else None


def bauen(st: dict, statefile: Path, pr_body: bool) -> str:
    root = st.get("root", ".")
    runs_dir = statefile.parent
    runde = letzte_runde(st)
    art = artefakte(runde)
    ctx, res, prov = art.get("ctx"), art.get("result"), art.get("prov")
    gate = gate_der_runde(runs_dir, runde)

    task = st["task"]
    head = (runde or {}).get("head") or (ctx or {}).get("range", {}).get("head")
    jetzt_head = git(root, "rev-parse", "HEAD")
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    ergebnis = gate.get("gate") if gate else (st.get("stop_reason") or "kein Urteil")

    L: list[str] = []
    if not pr_body:
        L += [f"# Entscheidungsblatt — {task['id']}", ""]
    L += [f"**{task.get('title') or task['id']}**", "",
          f"| | |", "|---|---|",
          f"| Gate | **{ergebnis}** |",
          f"| Geprueft | `{head or '—'}` |",
          f"| Basis | `{st['base']}` |",
          f"| Branch | `{branch or '—'}` |",
          f"| Task | `{task['file']}` (sha256 `{task['sha256']}`) |",
          f"| Korrekturrunden | {st['spent']['corrections']} von "
          f"{st['limits']['max_correction_rounds']} |",
          f"| Zustand | `{st.get('state')}` — {st.get('stop_reason') or '—'} |", ""]

    L += ["> **`PASS` heisst: bereit fuer die menschliche Entscheidung.** Es heisst nicht",
          "> freigegeben und loest keinen Merge aus. Kein Programm in dieser Kette",
          "> uebernimmt etwas.", ""]

    if head and jetzt_head and head != jetzt_head:
        L += ["> ## ⚠ Diese Entscheidung gilt nicht fuer den aktuellen Stand", "",
              f"> Geprueft wurde `{head}`, der Arbeitsstand steht auf `{jetzt_head}`.",
              "> Was seither dazukam, ist **weder getestet noch geprueft**. Ein neuer Commit",
              "> braucht neue Tests und ein neues Review — sonst entscheidest du ueber",
              "> einen Stand, den niemand angesehen hat.", ""]
    elif head:
        L += [f"*Der Arbeitsstand steht auf dem geprueften Commit `{head[:12]}`.*", ""]

    # ---------------------------------------------------------------- Acceptance
    gefordert = (ctx or {}).get("task", {}).get("acceptance") or []
    bewertet = {i["item"]: i for i in ((res or {}).get("reviewed", {}).get("acceptance_items") or [])}
    if gefordert:
        L += ["## Acceptance", "", "| Kriterium aus der Task | Urteil | Begruendung |", "|---|---|---|"]
        offen = set(gate.get("acceptance_uncovered") or []) if gate else set()
        for k in gefordert:
            e = bewertet.get(k)
            if k in offen or not e:
                L.append(f"| {k} | **vom Reviewer nicht bewertet** | — |")
            else:
                L.append(f"| {k} | {SYMBOL.get(e.get('verdict'), e.get('verdict'))} | "
                         f"{(e.get('why') or '—')[:200]} |")
        L.append("")
        if gate and gate.get("acceptance_unbound"):
            L += ["**Der Reviewer hat zusaetzlich beurteilt, was die Task nicht fordert:**", ""]
            L += [f"- {x}" for x in gate["acceptance_unbound"]] + [""]

    # ---------------------------------------------------------------- Tests
    t = (ctx or {}).get("tests") or {}
    L += ["## Testnachweis", ""]
    if t.get("ran"):
        L += [f"- Kommando: `{t.get('command')}`",
              f"- Exit `{t.get('exit_code')}` — **{'bestanden' if t.get('passed') else 'FEHLGESCHLAGEN'}**",
              f"- gelaufen gegen `{t.get('ran_against')}`"
              + ("  ✓ derselbe Commit" if t.get("ran_against") == head else "  ⚠ **nicht der geprueften Commit**"),
              f"- isoliert im Worktree: {'ja' if t.get('isolated') else '**nein**'}",
              f"- Protokoll: `{t.get('output_file')}` ({t.get('output_bytes')} B, "
              f"sha256 `{t.get('output_sha256')}`)", ""]
    else:
        L += [f"**Keine Tests gelaufen** — {t.get('why') or 'ohne Angabe'}", ""]

    # ---------------------------------------------------------------- Befunde
    for schl, titel in (("blocking", "Blockierende Befunde"),
                        ("governance_conflicts", "Governance-Konflikte"),
                        ("unproven_claims", "Unbelegte Behauptungen der Lieferung"),
                        ("non_blocking", "Nicht blockierende Anmerkungen")):
        eintraege = (res or {}).get(schl) or []
        if not eintraege:
            continue
        L += [f"## {titel} ({len(eintraege)})", ""]
        for e in eintraege:
            kopf = e.get("what") or e.get("claim") or e.get("id") or "—"
            L += [f"### {kopf}"]
            for k, lbl in (("where", "Fundstelle"), ("why", "Warum"), ("class", "Klasse"),
                           ("id", "ID"), ("suggested_fix", "Vorschlag"),
                           ("what_would_prove_it", "Was es belegen wuerde")):
                if e.get(k):
                    L += [f"- **{lbl}:** {e[k]}"]
            L += [""]

    # ---------------------------------------------------------------- Offene Fragen
    fragen = (ctx or {}).get("task", {}).get("open") or []
    fehlt = (res or {}).get("missing_context") or []
    if fragen or fehlt:
        L += ["## Offene Fragen", ""]
        if fragen:
            L += ["**Die Task legt dem Menschen ausdruecklich vor:**", ""]
            L += [f"- {x}" for x in fragen] + [""]
        if fehlt:
            L += ["**Der Reviewer hat vermisst:**", ""]
            L += [f"- {x}" for x in fehlt] + [""]

    # ---------------------------------------------------------------- Unabhaengigkeit
    unab = (gate or {}).get("independence") or (prov or {}).get("independence") or {}
    zustand = {True: "nachgewiesen", False: "**VERLETZT**", None: "**nicht nachgewiesen**"}.get(
        unab.get("separated"), "unbekannt")
    L += ["## Unabhaengigkeit des Urteils", "",
          f"- Familientrennung: {zustand}",
          f"- Builder: `{(unab.get('builder') or {}).get('model') or '—'}` "
          f"({unab.get('builder_family') or '?'})",
          f"- Reviewer: `{(unab.get('reviewer') or {}).get('model') or (prov or {}).get('model') or '—'}` "
          f"({unab.get('reviewer_family') or '?'})",
          f"- Beleg: {unab.get('evidence') or '—'}",
          f"- {unab.get('why') or ''}", ""]
    if prov and prov.get("source_requests"):
        L += ["**Der Reviewer hat Originalquellen nachgefordert:**", ""]
        for q in prov["source_requests"]:
            L.append(f"- `{q.get('asked')}` → "
                     + (f"`{q.get('resolved')}` ({q.get('bytes')} B)" if not q.get("refused_why")
                        else f"**nicht geliefert**: {q['refused_why']}"))
        L.append("")

    # ---------------------------------------------------------------- Weggelassen
    weg = (ctx or {}).get("omitted") or []
    L += ["## Was der Reviewer NICHT gesehen hat", ""]
    if weg:
        L += [f"- `{o['path']}` — {o['why']}" for o in weg]
        L += ["", "*Ein Urteil, das von einem dieser Posten abhaengt, ist kein Urteil ueber "
              "die ganze Lieferung.*", ""]
    else:
        L += ["Nichts. Diff und Testprotokoll gingen vollstaendig mit.", ""]

    # ---------------------------------------------------------------- Aufwand
    sp = st["spent"]
    L += ["## Aufwand dieses Laufs", "",
          f"- Korrekturrunden: {sp['corrections']} von {st['limits']['max_correction_rounds']}",
          f"- Builder-Aufrufe: {sp['builder_calls']} von {st['limits']['max_builder_calls']}",
          f"- Reviewer-Aufrufe: {sp['reviewer_calls']} von {st['limits']['max_reviewer_calls']}",
          f"- Laufzeit: {sp.get('wall_minutes')} min von {st['limits']['max_wall_minutes']} min",
          f"- Kosten: " + (f"{sp['cost_usd']} USD" if sp.get("cost_usd") is not None
                           else "nicht berechnet (keine Token-Preise konfiguriert)"), ""]
    if (prov or {}).get("usage_total", {}).get("prompt_tokens"):
        u = prov["usage_total"]
        L += [f"- Reviewer-Token: {u.get('prompt_tokens')} ein / "
              f"{u.get('completion_tokens')} aus", ""]

    # ---------------------------------------------------------------- Verlauf
    L += ["## Verlauf", "", "| Runde | Bau | Paket | Review | Gate | Commit |",
          "|---|---|---|---|---|---|"]
    for r in st.get("rounds", []):
        def z(n: str) -> str:
            s = (r.get(n) or {}).get("status")
            return s or "—"
        L.append(f"| {r['n']} | {z('build')} | {z('package')} | {z('review')} | "
                 f"{(r.get('gate') or {}).get('result', '—')} | "
                 f"`{(r.get('head') or '—')[:12]}` |")
    L += ["", "---", "",
          f"*Erzeugt aus `{statefile}` — keine Zahl hier ist von Hand gepflegt.*"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Entscheidungsblatt aus dem Laufzustand erzeugen")
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pr-body", action="store_true", help="nur den Rumpf, fuer den Pull Request")
    a = ap.parse_args()

    sf = Path(a.state)
    st = lade(sf)
    if not st:
        print(f"Laufzustand nicht lesbar: {sf}", file=sys.stderr)
        return 1
    text = bauen(st, sf, a.pr_body)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"geschrieben: {a.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
