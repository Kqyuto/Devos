#!/usr/bin/env python3
"""Prueft die Reviewer-Antwort und berechnet das Gate — deterministisch, ohne Modell.

Was dieses Skript ausdruecklich NICHT tut: uebernehmen. Es sagt nur, ob der
Mensch ueberhaupt vor einer Entscheidung steht. Der Merge bleibt Handarbeit.

Statusvorrang — der staerkste Grund gewinnt:

    INSUFFICIENT_CONTEXT > CONFLICT > INDEPENDENCE_UNPROVEN > BLOCKED
                         > CHANGES_REQUIRED > PASS

`INDEPENDENCE_UNPROVEN` kann kein Reviewer erklaeren; nur diese Maschine setzt
ihn. Er heisst: das Urteil mag richtig sein, aber es ist nicht nachweislich das
Urteil einer anderen Modellfamilie — und genau darauf beruht das Verfahren.

Was mechanisch durchgesetzt wird — sonst sind es nur gut gemeinte Saetze:

  1. Ohne gueltigen Kontext ist `PASS` unerreichbar. Ein fehlender, nicht
     lesbarer oder strukturell falscher `--context` ist ein Befund, kein
     neutraler Zustand.                                           (F01, G01)
  2. Identitaet exakt: task_id gleich, base und head als VOLLE SHAs gleich. (F01)
  3. Volle Schemapruefung BEIDER Dateien — Ergebnis *und* Kontext.  (F02, G01)
  4. Acceptance ist Gate-Bedingung: `not_met` schliesst PASS aus,
     `unverifiable` erzwingt INSUFFICIENT_CONTEXT — und jeder geforderte Punkt
     muss ueberhaupt bewertet worden sein.                         (F02, G02)
  5. Nicht leeres `blocking` schliesst PASS aus.
  6. `governance_conflicts` erzwingt CONFLICT — aus JEDEM Ausgangsstatus. (N02)
  7. Der Testnachweis wird nachgerechnet, nicht geglaubt: Exit-Code gegen
     `passed`, Testrevision gegen den geprueften Head.             (F03, G03)
  8. Die Trennung der Modellfamilien muss NACHGEWIESEN sein. Unbekannt ist
     nicht verschieden.                                                 (G04)

Exit-Codes:  0 MERGEABLE_PENDING_HUMAN · 1 CHANGES_REQUIRED
             2 HUMAN_DECISION_REQUIRED  · 3 INVALID_RESULT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonschema_mini as J          # noqa: E402
import model_family as MF            # noqa: E402

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
SCHEMA = SCHEMA_DIR / "review_result.schema.json"
CTX_SCHEMA = SCHEMA_DIR / "review_context.schema.json"
DISPATCH_SCHEMA = SCHEMA_DIR / "review_dispatch.schema.json"

RANK = {"PASS": 0, "CHANGES_REQUIRED": 1, "BLOCKED": 2, "INDEPENDENCE_UNPROVEN": 3,
        "CONFLICT": 4, "INSUFFICIENT_CONTEXT": 5}


def escalate(cur: str, to: str, reason: str, log: list[str]) -> str:
    """Hebt den Status an, nie ab. Der staerkste Grund gewinnt.

    Protokolliert wird JEDER Grund, der greift — auch der, der den Status nicht
    mehr anhebt, weil schon ein staerkerer gilt. Sonst verschwindet ein zweiter,
    unabhaengiger Befund spurlos aus dem Bericht, und der Mensch entscheidet
    ueber einen Ausschnitt.
    """
    if RANK[to] > RANK[cur]:
        log.append(f"{reason} — Status {cur} auf {to} angehoben")
        return to
    log.append(f"{reason} — greift, Status bleibt {cur} (dort gilt bereits ein staerkerer Grund)")
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


def load_context(pfad: str | None) -> tuple[dict | None, str | None]:
    """Laedt den Kontext und prueft ihn STRUKTURELL.

    Eine Datei, die als JSON parst, ist noch kein Kontext. Vor dieser Pruefung
    stuerzte das Gate an einer JSON-Liste mit AttributeError ab — und ein
    Absturz hat Exit 1, was der Aufrufer als CHANGES_REQUIRED liest. Ein
    Programmfehler, der als Gate-Urteil erscheint, ist schlimmer als gar kein
    Gate.                                                               (G01)
    """
    if not pfad:
        return None, "kein --context uebergeben"
    p = Path(pfad)
    if not p.exists():
        return None, f"--context {pfad} existiert nicht"
    try:
        ctx = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        return None, f"--context nicht lesbar: {ex}"
    fehler = J.validate(ctx, json.loads(CTX_SCHEMA.read_text(encoding="utf-8")))
    if fehler:
        return None, ("Kontext verletzt schema/review_context.schema.json: "
                      + "; ".join(fehler[:4]) + (f" (+{len(fehler) - 4} weitere)" if len(fehler) > 4 else ""))
    return ctx, None


def _norm(s: str) -> str:
    """Vergleichsform eines Acceptance-Punktes — Formatierung darf nicht entscheiden."""
    s = re.sub(r"[`*_]", "", (s or "").strip().lower())
    return re.sub(r"\s+", " ", s).strip(" .;:,—-")


def acceptance_abdeckung(gefordert: list[str], bewertet: list[str]) -> tuple[list[str], list[str]]:
    """(nicht bewertete Forderungen, Bewertungen ohne Entsprechung).

    Vollstaendige normalisierte Texte muessen eindeutig eins zu eins passen.
    Ein gemeinsamer Satzanfang bewertet keine seiner Fortsetzungen. Doppelte
    Forderungen oder Bewertungen sind mehrdeutig und werden ausgewiesen.
    """
    gn = [(x, _norm(x)) for x in gefordert]
    bn = [(x, _norm(x)) for x in bewertet]

    gc, bc = Counter(n for _, n in gn), Counter(n for _, n in bn)
    offen = [roh for roh, n in gn if not n or gc[n] != 1 or bc[n] != 1]
    lose = [roh for roh, n in bn if not n or gc[n] != 1 or bc[n] != 1]
    return offen, lose


def load_dispatch(pfad: str | None, request: Path, context: str | None,
                  result: Path, ctx: dict | None) -> tuple[dict | None, list[str]]:
    """Prueft den lokalen Transportdatensatz und seine drei Artefaktbindungen.

    Hashes erkennen vertauschte/veraenderte Dateien. Sie authentisieren keinen
    Modellanbieter und schuetzen nicht vor gemeinsam gefaelschten Artefakten.
    Alte oder fehlerhafte Datensaetze sind kein gemessener Nachweis (G06).
    """
    if not pfad:
        return None, []
    try:
        prov = json.loads(Path(pfad).read_text(encoding="utf-8"))
        schema = json.loads(DISPATCH_SCHEMA.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        return None, [f"Transport-Provenienz nicht lesbar: {ex}"]
    errors = J.validate(prov, schema)
    if errors:
        return None, ["Transport-Provenienz verletzt Schema: " + "; ".join(errors[:4])]
    if prov["ok"] is not True:
        errors.append("Transport-Provenienz: ok ist nicht true")
    attempts = prov["attempts"]
    if not attempts or prov["calls"] != len(attempts):
        errors.append("Transport-Provenienz: calls/attempts fehlen oder widersprechen sich")
    elif attempts[-1]["schema_errors"] or [a["runde"] for a in attempts] != list(range(1, len(attempts) + 1)):
        errors.append("Transport-Provenienz: kein erfolgreicher letzter Versuch oder ungueltige Reihenfolge")
    for key in ("endpoint_host", "model", "started_at", "finished_at"):
        if not prov[key].strip():
            errors.append(f"Transport-Provenienz: {key} ist leer")
    try:
        start = datetime.fromisoformat(prov["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(prov["finished_at"].replace("Z", "+00:00"))
        if start.utcoffset() is None or end.utcoffset() is None or end < start:
            raise ValueError("Zeitfolge oder Zeitzone fehlt")
    except (ValueError, TypeError):
        errors.append("Transport-Provenienz: ungueltige Zeitangaben")
    if prov["model"] != prov["reviewer"].get("model"):
        errors.append("Transport-Provenienz: model und reviewer.model widersprechen sich")
    if ctx is not None and prov["builder"] != ctx.get("builder"):
        errors.append("Transport-Provenienz: Builder stimmt nicht mit dem Kontext ueberein")
    for key, path in (("request_sha256", request),
                      ("context_sha256", Path(context) if context else None),
                      ("result_sha256", result)):
        expected = prov.get(key)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(f"Transport-Provenienz: {key} fehlt oder ist kein voller SHA256")
            continue
        try:
            content = path.read_bytes() if path is not None else None
        except OSError:
            content = None
        if content is None or hashlib.sha256(content).hexdigest() != expected:
            errors.append(f"Transport-Provenienz: {key} passt nicht zum vorgelegten Artefakt")
        elif key == "request_sha256" and prov["request_bytes"] != len(content):
            errors.append("Transport-Provenienz: request_bytes passt nicht zum Paket")
    return (None if errors else prov), errors


def unabhaengigkeit(ctx: dict | None, r: dict, prov: dict | None) -> dict:
    """Nachweis der Familientrennung — aus der Provenienz, ersatzweise selbsterklaert."""
    builder = (ctx or {}).get("builder") if isinstance((ctx or {}).get("builder"), dict) else None
    reviewer, quelle = None, "kein Nachweis"
    if isinstance(prov, dict) and isinstance(prov.get("reviewer"), dict):
        reviewer, quelle = prov["reviewer"], "Transport-Provenienz (gemessen)"
    elif isinstance(r.get("reviewer"), dict) and r["reviewer"].get("model"):
        fam, woher = MF.familie(r["reviewer"].get("model"), None, r["reviewer"].get("model_family"))
        reviewer = {"model": r["reviewer"].get("model"), "family": fam, "declared_via": woher}
        quelle = "Selbstauskunft des Reviewers (nicht gemessen)"
    t = MF.trennung(builder, reviewer)
    t["evidence"] = quelle
    t["builder"] = builder
    t["reviewer"] = reviewer
    return t


def gate(r: dict, ctx: dict | None, ctx_problem: str | None,
         prov: dict | None) -> tuple[str, int, list[str], dict]:
    log: list[str] = []
    status = r["status"]
    detail: dict = {}

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

    # ------------------------------------------------ alles Weitere braucht den Kontext
    if ctx is None:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          "kein Kontext geladen — PASS ist ohne Paketbindung unerreichbar", log)
        detail["independence"] = unabhaengigkeit(None, r, prov)
        return _abschluss(status, log, detail)

    # --- Abdeckung: hat der Reviewer ueberhaupt bewertet, was gefordert war? (G02)
    gefordert = ctx["task"].get("acceptance") or []
    if not gefordert:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          "die Task nennt keinen einzigen Acceptance-Punkt — es gibt nichts, "
                          "wogegen geprueft werden koennte", log)
    offen, lose = acceptance_abdeckung(gefordert, [i["item"] for i in items])
    detail["acceptance_uncovered"] = offen
    detail["acceptance_unbound"] = lose
    if offen:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          f"{len(offen)} von {len(gefordert)} Acceptance-Punkt(en) vom Reviewer "
                          f"nicht bewertet: {'; '.join(x[:60] for x in offen[:3])}", log)
    if lose:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          f"{len(lose)} Bewertung(en) ohne Entsprechung in der Task — der Reviewer "
                          f"hat etwas anderes beurteilt als gefordert: "
                          f"{'; '.join(x[:60] for x in lose[:3])}", log)

    # --- Testnachweis nachrechnen, nicht glauben (G03)
    t = ctx["tests"]
    head = ctx["range"]["head"]
    if t.get("ran") is not True:
        status = escalate(status, "CHANGES_REQUIRED",
                          f"im Review-Paket liefen keine Tests ({t.get('why') or 'ohne Angabe'})", log)
    else:
        ec = t.get("exit_code")
        if ec is None:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              "Testlauf ohne Exit-Code — `passed` waere die einzige Quelle, "
                              "und eine Behauptung ohne Exit-Code ist kein Nachweis", log)
        elif ec != 0 and t.get("passed") is True:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              f"das Paket widerspricht sich: exit_code={ec}, aber passed=true — "
                              "welches Feld luegt, ist von hier aus nicht entscheidbar", log)
        elif ec == 0 and t.get("passed") is not True:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              f"das Paket widerspricht sich: exit_code=0, aber passed="
                              f"{t.get('passed')!r}", log)
        elif ec != 0:
            status = escalate(status, "CHANGES_REQUIRED", f"Tests fehlgeschlagen (Exit {ec})", log)

        if t.get("isolated") is not True:
            status = escalate(status, "CHANGES_REQUIRED",
                              "Tests liefen nicht in einem isolierten Worktree — lokaler "
                              "Reparaturcode kann einen aelteren Stand gruen faerben", log)
        ra = t.get("ran_against")
        if not ra:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              "der Testlauf nennt keine Revision — der Nachweis ist an nichts "
                              "gebunden", log)
        elif ra != head:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              f"die Tests liefen gegen eine andere Revision als die gepruefte: "
                              f"{ra[:12]} statt {head[:12]}", log)

    # --- Identitaet: Urteil und Lieferung muessen dieselbe sein (F01)
    cid = ctx["task"].get("id")
    if cid and r["task_id"] != cid:
        status = escalate(status, "INSUFFICIENT_CONTEXT",
                          f"Reviewer nennt task_id {r['task_id']!r}, das Paket {cid!r}", log)
    for k in ("base", "head"):
        rv, cv = r["reviewed_range"].get(k, ""), ctx["range"].get(k, "")
        if not rv or not cv or rv != cv:
            status = escalate(status, "INSUFFICIENT_CONTEXT",
                              f"reviewed_range.{k}={rv!r} ungleich Paket {cv!r} "
                              "(exakter Voll-SHA verlangt)", log)

    # --- Familientrennung: die Gegenmassnahme selbst pruefen (G04)
    unab = unabhaengigkeit(ctx, r, prov)
    detail["independence"] = unab
    if unab["separated"] is False:
        status = escalate(status, "INDEPENDENCE_UNPROVEN", f"Familientrennung verletzt: {unab['why']}", log)
    elif unab["separated"] is None:
        status = escalate(status, "INDEPENDENCE_UNPROVEN", unab["why"], log)
    elif unab["evidence"].startswith("Selbstauskunft"):
        log.append(f"Familientrennung nur selbsterklaert ({unab['why']}) — nicht am Transport "
                   "gemessen; steht dem Menschen vor Augen")

    if ctx.get("omitted"):
        log.append(f"{len(ctx['omitted'])} Posten waren nicht im Paket — steht dem Menschen vor Augen")

    return _abschluss(status, log, detail)


def _abschluss(status: str, log: list[str], detail: dict) -> tuple[str, int, list[str], dict]:
    code = {"PASS": 0, "CHANGES_REQUIRED": 1}.get(status, 2)
    name = {0: "MERGEABLE_PENDING_HUMAN", 1: "CHANGES_REQUIRED"}.get(code, "HUMAN_DECISION_REQUIRED")
    return f"{name} [{status}]", code, log, detail


def main() -> int:
    ap = argparse.ArgumentParser(description="Reviewer-Antwort pruefen und Gate berechnen")
    ap.add_argument("result")
    ap.add_argument("--context", default=None,
                    help="review_context.json der Lieferung — ohne sie ist PASS unerreichbar")
    ap.add_argument("--dispatch", default=None,
                    help="review_dispatch.json — der gemessene Nachweis der Familientrennung")
    ap.add_argument("--request", default=None,
                    help="zugehoeriges Paket; Standard: REVIEW-REQUEST.md neben dem Ergebnis")
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
            print(json.dumps({"gate": "INVALID_RESULT", "exit_code": 3, "errors": errs},
                             ensure_ascii=False, indent=2))
        else:
            print("INVALID_RESULT")
            for e in errs:
                print(f"  - {e}")
        return 3

    ctx, ctx_problem = load_context(a.context)

    request = Path(a.request) if a.request else Path(a.result).parent / "REVIEW-REQUEST.md"
    prov, prov_errors = load_dispatch(a.dispatch, request, a.context, Path(a.result), ctx)

    verdict, code, log, detail = gate(r, ctx, ctx_problem, prov)
    log.extend(prov_errors)
    items = r["reviewed"].get("acceptance_items") or []
    payload = {
        "gate": verdict, "exit_code": code, "declared_status": r["status"],
        "escalations": log,
        "blocking": len(r["blocking"]), "non_blocking": len(r.get("non_blocking") or []),
        "governance_conflicts": len(r.get("governance_conflicts") or []),
        "unproven_claims": len(r.get("unproven_claims") or []),
        "acceptance_not_met": [i["item"] for i in items if i.get("verdict") == "not_met"],
        "acceptance_unverifiable": [i["item"] for i in items if i.get("verdict") == "unverifiable"],
        "acceptance_uncovered": detail.get("acceptance_uncovered", []),
        "acceptance_unbound": detail.get("acceptance_unbound", []),
        "independence": detail.get("independence"),
        "dispatch_errors": prov_errors,
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
    u = payload["independence"] or {}
    print(f"  {'Familientrennung':<22}: "
          f"{ {True: 'nachgewiesen', False: 'VERLETZT', None: 'nicht nachgewiesen'}[u.get('separated')] }"
          f" ({u.get('builder_family') or '?'} / {u.get('reviewer_family') or '?'}, "
          f"{u.get('evidence', '—')})")
    for key, lbl in (("acceptance_not_met", "Acceptance nicht erfuellt"),
                     ("acceptance_unverifiable", "Acceptance nicht pruefbar"),
                     ("acceptance_uncovered", "Acceptance NICHT BEWERTET"),
                     ("acceptance_unbound", "bewertet, aber nicht gefordert")):
        for i in payload[key]:
            print(f"  {lbl}: {i}")
    if log:
        print("  maschinelle Befunde:")
        for x in log:
            print(f"      - {x}")
    for b in r["blocking"]:
        print(f"  BLOCKER  {b['where']} — {b['what']}")
    print("\n  Der Merge bleibt beim Menschen. Dieses Skript uebernimmt nichts.")
    return code


if __name__ == "__main__":
    sys.exit(main())
