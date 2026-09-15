#!/usr/bin/env python3
"""Misst den Entwicklungsprozess — damit die Tragfaehigkeit gerechnet und nicht geglaubt wird.

Was diese Messung NICHT kann, und zwar grundsaetzlich (Befund F05):

  Ein Pilot unter REDUZIERTEM Umfang (V-DEV-7) kann die Last des VOLLEN
  Glied-0-Verfahrens weder bestaetigen noch widerlegen. Wer den Umfang senkt und
  dann misst, misst den gesenkten Umfang. Deshalb traegt jede Lieferung ein
  `scope` ("reduced" | "full"), und ein Urteil ueber den Vollumfang wird nur aus
  Lieferungen mit scope="full" gebildet. Fehlen sie, sagt dieses Skript das und
  urteilt nicht.

  Ebenso: `changed_files` ist NICHT dasselbe wie Pflichtartefakte. Die
  Assay-Behauptung "12-20 Artefakte je Lieferung" meint K2-Annahmenblock,
  K1-Rechnung, Lint-Trace, Baugraph-Nachfuehrung, Lastdeklaration und die sechs
  Pflichtlaeufe — nicht die Zahl der beruehrten Dateien. Pflichtartefakte werden
  ausdruecklich gebucht, nicht aus git geraten.

Eingabe: devos/deliveries.jsonl — append-only, eine Zeile je Ereignis.

  {"delivery":"TASK-001","event":"task_opened","at":"...","base":"<voller SHA>","scope":"reduced"}
  {"delivery":"TASK-001","event":"human_minutes","minutes":25,"what":"Task geschnitten"}
  {"delivery":"TASK-001","event":"builder_minutes","minutes":95}
  {"delivery":"TASK-001","event":"reviewer_minutes","minutes":12}
  {"delivery":"TASK-001","event":"artifact","name":"K2-Annahmenblock","minutes":20}
  {"delivery":"TASK-001","event":"suspended_artifact","name":"K1-Rechnung"}
  {"delivery":"TASK-001","event":"review_round","blocking":2,"false_alarms":1}
  {"delivery":"TASK-001","event":"accepted","at":"...","commit":"<voller SHA>"}

Gegen-Metrik gegen das Optimieren der Zielgroesse: `minutes_per_changed_line`.
Wer die Durchlaufzeit senkt, indem er Lieferungen leert, verschlechtert sie.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def parse_at(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def git_numstat(root: str, base: str, head: str) -> tuple[int, int, str | None]:
    """(geaenderte Zeilen, geaenderte Dateien, Fehler). Fehler wird ausgewiesen, nie zu 0."""
    r = subprocess.run(["git", "-C", root, "diff", "--numstat", f"{base}..{head}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return 0, 0, f"git diff {base[:12]}..{head[:12]} fehlgeschlagen: {r.stderr.strip()[:160]}"
    lines = files = 0
    for row in r.stdout.splitlines():
        p = row.split("\t")
        if len(p) != 3:
            continue
        files += 1
        for v in p[:2]:
            if v.isdigit():
                lines += int(v)
    return lines, files, None


def split_events(raw: list[dict]) -> tuple[list[dict], list[str]]:
    """Trennt Ereignisse von Kommentaren und meldet kaputte Zeilen.

    Eine Zeile ohne `delivery`/`event` ist ein Datenfehler und wird gemeldet, nicht
    verschluckt und nicht als Absturz weitergereicht. Reine `_comment`-Objekte sind
    zulaessig, weil JSONL keine Kommentare kennt.
    """
    events, bad = [], []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            bad.append(f"Zeile {i}: kein Objekt")
        elif set(e) <= {"_comment"}:
            continue
        elif "delivery" not in e or "event" not in e:
            bad.append(f"Zeile {i}: 'delivery' oder 'event' fehlt — {json.dumps(e, ensure_ascii=False)[:90]}")
        else:
            events.append(e)
    return events, bad


def aggregate(events: list[dict], root: str) -> list[dict]:
    by: dict[str, dict] = {}
    for e in events:
        d = by.setdefault(e["delivery"], {
            "delivery": e["delivery"], "scope": None, "human_minutes": 0, "builder_minutes": 0,
            "reviewer_minutes": 0, "artifact_minutes": 0, "review_rounds": 0, "blockers": 0,
            "false_alarms": 0, "opened": None, "accepted": None, "base": None, "head": None,
            "mandatory_artifacts": [], "suspended_artifacts": [], "git_error": None,
        })
        ev = e["event"]
        if ev == "task_opened":
            d["opened"], d["base"], d["scope"] = e["at"], e.get("base"), e.get("scope")
        elif ev == "accepted":
            d["accepted"], d["head"] = e["at"], e.get("commit")
        elif ev in ("human_minutes", "builder_minutes", "reviewer_minutes"):
            d[ev] += int(e["minutes"])
        elif ev == "artifact":
            d["mandatory_artifacts"].append(e["name"])
            d["artifact_minutes"] += int(e.get("minutes", 0))
        elif ev == "suspended_artifact":
            d["suspended_artifacts"].append(e["name"])
        elif ev == "review_round":
            d["review_rounds"] += 1
            d["blockers"] += int(e.get("blocking", 0))
            d["false_alarms"] += int(e.get("false_alarms", 0))

    for d in by.values():
        d["complete"] = False   # erst nach der git-Messung entscheidbar
        d["lead_time_hours"] = (round((parse_at(d["accepted"]) - parse_at(d["opened"])).total_seconds() / 3600, 2)
                                if d["opened"] and d["accepted"] else None)
        if d["base"] and d["head"]:
            d["changed_lines"], d["changed_files"], d["git_error"] = git_numstat(root, d["base"], d["head"])
            if d["git_error"]:
                d["changed_lines"] = d["changed_files"] = None
        else:
            d["changed_lines"] = d["changed_files"] = None
        total = d["human_minutes"] + d["builder_minutes"] + d["reviewer_minutes"]
        d["total_minutes"] = total
        d["human_share"] = round(d["human_minutes"] / total, 3) if total else None
        d["minutes_per_changed_line"] = round(total / d["changed_lines"], 3) if d["changed_lines"] else None
        d["blocker_precision"] = (round((d["blockers"] - d["false_alarms"]) / d["blockers"], 3)
                                  if d["blockers"] else None)
        d["mandatory_artifact_count"] = len(d["mandatory_artifacts"])
        # Vollstaendig gemessen heisst: eroeffnet, abgenommen, UND die git-Messung
        # ist gelungen. Eine Lieferung, deren Ref kaputt ist, ist nicht gemessen -
        # sie darf keine Stichprobe fuellen. (Befund F05)
        d["complete"] = bool(d["opened"] and d["accepted"] and d["base"] and d["head"]
                             and not d["git_error"] and d["changed_lines"] is not None)
    return sorted(by.values(), key=lambda d: d["opened"] or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prozessmetriken je Lieferung")
    ap.add_argument("--log", default="devos/deliveries.jsonl")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    p = Path(a.log)
    if not p.exists():
        print(f"kein Log unter {p} — noch nichts gemessen. Das ist ein Befund, kein neutraler Zustand.",
              file=sys.stderr)
        return 1
    raw = []
    for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        try:
            raw.append(json.loads(l))
        except json.JSONDecodeError as ex:
            print(f"BEFUND Zeile {i}: kein gueltiges JSON — {ex}", file=sys.stderr)
            return 1
    events, bad = split_events(raw)
    if bad:
        for b in bad:
            print(f"BEFUND {b}", file=sys.stderr)
        return 1
    rows = aggregate(events, str(Path(a.root).resolve()))
    done = [d for d in rows if d["complete"]]
    full = [d for d in done if d["scope"] == "full"]

    if a.json:
        print(json.dumps({"deliveries": rows, "n_all": len(rows), "n_complete": len(done),
                          "n_full_scope": len(full)}, ensure_ascii=False, indent=2))
        return 0

    print(f"Lieferungen gesamt: {len(rows)} · davon abgeschlossen und vollstaendig gemessen: {len(done)}\n")
    hdr = ("Lieferung", "Umfang", "fertig", "Std.ges", "Mensch%", "Runden", "Blocker",
           "Fehlalarm", "Pflichtart.", "ausgesetzt", "Dateien", "Zeilen", "min/Zeile")
    print("| " + " | ".join(hdr) + " |")
    print("|" + "---|" * len(hdr))
    for d in rows:
        print("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            d["delivery"], d["scope"] or "—", "ja" if d["complete"] else "nein",
            round(d["total_minutes"] / 60, 2) if d["total_minutes"] else "—",
            f"{d['human_share']*100:.0f}" if d["human_share"] is not None else "—",
            d["review_rounds"], d["blockers"], d["false_alarms"],
            d["mandatory_artifact_count"], len(d["suspended_artifacts"]),
            d["changed_files"] if d["changed_files"] is not None else "—",
            d["changed_lines"] if d["changed_lines"] is not None else "—",
            d["minutes_per_changed_line"] if d["minutes_per_changed_line"] is not None else "—"))

    for d in rows:
        if d["git_error"]:
            print(f"\nBEFUND {d['delivery']}: {d['git_error']}")

    print()
    print("Zur Assay-Behauptung '12-20 Pflichtartefakte je Lieferung':")
    if not full:
        susp = sorted({s for d in done for s in d["suspended_artifacts"]})
        print(f"  KEIN URTEIL. Keine der {len(done)} abgeschlossenen Lieferungen lief unter scope='full'.")
        print("  Ein reduzierter Pilot misst den reduzierten Umfang, nie den vollen.")
        if susp:
            print(f"  Ausgesetzt und als offene Last gefuehrt: {', '.join(susp)}")
    else:
        n = sum(d["mandatory_artifact_count"] for d in full) / len(full)
        m = sum(d["artifact_minutes"] for d in full) / len(full)
        print(f"  {len(full)} Lieferung(en) mit scope='full': im Mittel {n:.1f} gebuchte Pflichtartefakte, "
              f"{m:.0f} min je Lieferung dafuer.")
        print(f"  n={len(full)} — {'noch keine belastbare Aussage (n<3)' if len(full) < 3 else 'vergleichbar mit der Behauptung'}.")

    print()
    if len(done) < 3:
        print(f"n={len(done)} vollstaendig gemessene Lieferungen < 3 — keine Aussage ueber den Durchsatz.")
        return 0

    tot = sum(d["total_minutes"] for d in done)
    hum = sum(d["human_minutes"] for d in done)
    print(f"Ueber {len(done)} abgeschlossene Lieferungen:")
    print(f"  Menschenanteil          : {hum/tot*100:.0f} %" if tot else "  Menschenanteil: —")
    print(f"  Gesamtzeit              : {tot/60:.1f} h  =>  bei 14 h/Woche {tot/60/14:.1f} Wochen, "
          f"bei 6 h/Woche {tot/60/6:.1f} Wochen")

    return 0


if __name__ == "__main__":
    sys.exit(main())
