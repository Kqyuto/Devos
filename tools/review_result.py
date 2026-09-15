#!/usr/bin/env python3
"""Prueft die Reviewer-Antwort und berechnet das Gate — deterministisch, ohne Modell.

Was dieses Skript ausdruecklich NICHT tut: uebernehmen. Es sagt nur, ob der
Mensch ueberhaupt vor einer Entscheidung steht. Der Merge bleibt Handarbeit.

Statusvorrang (Runde 2, Befund N02) — der staerkste Grund gewinnt:

    INSUFFICIENT_CONTEXT > CONFLICT > BLOCKED > CHANGES_REQUIRED > PASS

Was mechanisch durchgesetzt wird — sonst sind es nur gut gemeinte Saetze:

  1. Ohne gueltigen Kontext ist `PASS` unerreichbar. Ein fehlender oder nicht
     lesbarer `--context` ist ein Befund, kein neutraler Zustand.   (F01)
  2. Identitaet exakt: task_id gleich, base und head als VOLLE SHAs gleich.
     Praefixvergleiche sind hier wertlos.                            (F01)
  3. Volle Schemapruefung gegen review_result.schema.json — Typen, Enums,
     verschachtelte Pflichtfelder, additionalProperties.             (F02)
  4. Acceptance ist Gate-Bedingung: `not_met` schliesst PASS aus,
     `unverifiable` erzwingt INSUFFICIENT_CONTEXT.                   (F02)
  5. Nicht leeres `blocking` schliesst PASS aus.
  6. `governance_conflicts` erzwingt CONFLICT — aus JEDEM Ausgangsstatus.  (N02)
  7. Fehlende, nicht gelaufene oder fehlgeschlagene Tests schliessen PASS aus;
     Tests, die nicht gegen den geprueften Head liefen, ebenso.      (F03)

Exit-Codes:  0 MERGEABLE_PENDING_HUMAN · 1 CHANGES_REQUIRED
             2 HUMAN_DECISION_REQUIRED  · 3 INVALID_RESULT
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonschema_mini as J  # noqa: E402

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "review_result.schema.json"
RANK = {"PASS": 0, "CHANGES_REQUIRED": 1, "BLOCKED": 2, "CONFLICT": 3, "INSUFFICIENT_CONTEXT": 4}
UNRANK = {v: k for k, v in RANK.items()}


def escalate(cur: str, to: str, reason: str, log: list[str]) -> str:
    """Hebt den Status an, nie ab. Der staerkste Grund gewinnt."""
    if RANK[to] > RANK[cur]:
        log.append(f"{reason} — Status {cur} auf {to} angehoben")
        return to
    return cur


def validate(r: dict) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    e = J.validate(r, schema)
    if e:
        return e
    for i, b in enumerate(r.get("blocking") or []):
        for k in ("what", "where", "why"):
            if not b.get(k):
                e.append(f"blocking[{i}].{k} leer — ein Befund ohne Fundstelle ist kein Befund")
    if r.get("context_sufficient") is False and not (r.get("missing_context") or []):
        e.append("context_sufficient=false verlangt missing_context — sonst weiss niemand, was fehlte")
    return e


def gate(r: dict, ctx: dict | None, ctx_problem: str | None) -> tuple[str, int, list[str]]:
    log: list[str] = []
    status = r["status"]

    if ctx_problem:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          f"Kontext nicht verwertbar: {ctx_problem}", log)
        ctx = None

    if r["context_sufficient"] is False:
        status = escalate(status, "INSUFFICIENT_CONTEXT", "context_sufficient=false", log)

    if r.get("governance_conflicts"):
        status = escalate(status, "CONFLICT",
                          f"{len(r['governance_conflicts'])} Governance-Konflikt(e)", log)

    if r["blocking"]:
        status = escalate(status, "CHANGES_REQUIRED",
                          f"{len(r['blocking'])} blockierende(r) Befund(e)", log)

    items = r["reviewed"].get("acceptance_items") or []
    not_met = [i["item"] for i in items if i.get("verdict") == "not_met"]
    unver = [i["item"] for i in items if i.get("verdict") == "unverifiable"]
    if not_met:
        status = escalate(status, "CHANGES_REQUIRED", f"{len(not_met)} Acceptance-Punkt(e) not_met", log)
    if unver:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          f"{len(unver)} Acceptance-Punkt(e) unverifiable", log)

    tj = r["reviewed"].get("tests", {}).get("judged")
    if tj in ("inadequate", "absent", "not_run"):
        status = escalate(status, "CHANGES_REQUIRED", f"reviewed.tests.judged={tj}", log)

    if ctx is not None:
        t = ctx.get("tests", {})
        if not t.get("ran"):
            status = escalate(status, "CHANGES_REQUIRED", "im Review-Paket liefen keine Tests", log)
        elif not t.get("passed"):
            status = escalate(status, "CHANGES_REQUIRED", "Tests im Review-Paket fehlgeschlagen", log)
        elif not t.get("isolated"):
            status = escalate(status, "CHANGES_REQUIRED",
                              "Tests liefen nicht gegen den geprueften Stand (nicht isoliert)", log)

        cid = ctx.get("task", {}).get("id")
        if cid and r["task_id"] != cid:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              f"Reviewer nennt task_id {r['task_id']!r}, das Paket {cid!r}", log)
        for k in ("base", "head"):
            rv, cv = r["reviewed_range"].get(k, ""), ctx.get("range", {}).get(k, "")
            if not rv or not cv or rv != cv:
                status = escalate(status, "INSUFFICIENT_CONTEXT",
                                  f"reviewed_range.{k}={rv!r} ungleich Paket {cv!r} "
                                  "(exakter Voll-SHA verlangt)", log)
        if ctx.get("omitted"):
            log.append(f"{len(ctx['omitted'])} Posten waren nicht im Paket — steht dem Menschen vor Augen")
    else:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          "kein Kontext geladen — PASS ist ohne Paketbindung unerreichbar", log)

    code = {"PASS": 0, "CHANGES_REQUIRED": 1}.get(status, 2)
    name = {0: "MERGEABLE_PENDING_HUMAN", 1: "CHANGES_REQUIRED"}.get(code, "HUMAN_DECISION_REQUIRED")
    return f"{name} [{status}]", code, log


def main() -> int:
    ap = argparse.ArgumentParser(description="Reviewer-Antwort pruefen und Gate berechnen")
    ap.add_argument("result")
    ap.add_argument("--context", default=None, help="review_context.json der Lieferung — ohne sie ist PASS unerreichbar")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        r = json.loads(Path(a.result).read_text(encoding="utf-8"))
    except Exception as ex:
        print(f"INVALID_RESULT: nicht lesbar — {ex}", file=sys.stderr)
        return 3

    errs = validate(r)
    if errs:
        if a.json:
            print(json.dumps({"gate": "INVALID_RESULT", "errors": errs}, ensure_ascii=False, indent=2))
        else:
            print("INVALID_RESULT")
            for e in errs:
                print(f"  - {e}")
        return 3

    ctx, ctx_problem = None, None
    if not a.context:
        ctx_problem = "kein --context uebergeben"
    elif not Path(a.context).exists():
        ctx_problem = f"--context {a.context} existiert nicht"
    else:
        try:
            ctx = json.loads(Path(a.context).read_text(encoding="utf-8"))
        except Exception as ex:
            ctx_problem = f"--context nicht lesbar: {ex}"

    verdict, code, log = gate(r, ctx, ctx_problem)
    items = r["reviewed"].get("acceptance_items") or []
    payload = {
        "gate": verdict, "exit_code": code, "declared_status": r["status"],
        "escalations": log,
        "blocking": len(r["blocking"]), "non_blocking": len(r.get("non_blocking") or []),
        "governance_conflicts": len(r.get("governance_conflicts") or []),
        "unproven_claims": len(r.get("unproven_claims") or []),
        "acceptance_not_met": [i["item"] for i in items if i.get("verdict") == "not_met"],
        "acceptance_unverifiable": [i["item"] for i in items if i.get("verdict") == "unverifiable"],
    }
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return code

    print(f"GATE: {verdict}")
    print(f"  vom Reviewer erklaert : {r['status']}")
    for k, lbl in (("blocking", "blockierend"), ("non_blocking", "nicht blockierend"),
                   ("governance_conflicts", "Governance-Konflikte"),
                   ("unproven_claims", "unbelegte Behauptungen")):
        print(f"  {lbl:<22}: {payload[k]}")
    for key, lbl in (("acceptance_not_met", "Acceptance nicht erfuellt"),
                     ("acceptance_unverifiable", "Acceptance nicht pruefbar")):
        for i in payload[key]:
            print(f"  {lbl}: {i}")
    if log:
        print("  maschinelle Anhebungen:")
        for x in log:
            print(f"      - {x}")
    for b in r["blocking"]:
        print(f"  BLOCKER  {b['where']} — {b['what']}")
    print("\n  Der Merge bleibt beim Menschen. Dieses Skript uebernimmt nichts.")
    return code


if __name__ == "__main__":
    sys.exit(main())
