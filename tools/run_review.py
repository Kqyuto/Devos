#!/usr/bin/env python3
"""Ein Kommando statt vier Dateiuebergaben: erzeugen, versenden, Gate rechnen.

    python3 devos/tools/run_review.py --task devos/tasks/TASK-001-....md \
        --onto main --tests "python3 -m unittest discover"

Der Mensch startet das und liest das Ergebnis. Dazwischen traegt er nichts mehr.
Was er weiterhin tut - und nur er - ist entscheiden und uebernehmen.

Die drei Schritte bleiben getrennte Programme. Das ist Absicht: der Versender
darf kein Gate-Ergebnis erzeugen koennen, und das Gate darf nichts versenden.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE_NAMEN = {0: "MERGEABLE_PENDING_HUMAN", 1: "CHANGES_REQUIRED",
              2: "HUMAN_DECISION_REQUIRED", 3: "INVALID_RESULT"}


def schritt(nr: int, titel: str, cmd: list[str]) -> int:
    print(f"\n── {nr}/3 · {titel} " + "─" * max(0, 46 - len(titel)))
    return subprocess.run(cmd).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Lieferung erzeugen, pruefen lassen, Gate rechnen")
    ap.add_argument("--task", required=True)
    ap.add_argument("--base", default=None)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--onto", default=None)
    ap.add_argument("--tests", default=None)
    ap.add_argument("--out", default="devos/review")
    ap.add_argument("--dry-run", action="store_true", help="Paket erzeugen, nicht versenden")
    a = ap.parse_args()

    gen = [sys.executable, str(HERE / "review_request.py"), "--task", a.task, "--out", a.out]
    for flag, val in (("--base", a.base), ("--onto", a.onto), ("--tests", a.tests)):
        if val:
            gen += [flag, val]
    gen += ["--head", a.head]
    if schritt(1, "Review-Paket erzeugen", gen) != 0:  # noqa: E501
        print("\nAbbruch: Paket nicht erzeugt.", file=sys.stderr)
        return 5

    disp = [sys.executable, str(HERE / "review_dispatch.py"),
            "--request", f"{a.out}/REVIEW-REQUEST.md", "--out", f"{a.out}/review_result.json",
            "--context", f"{a.out}/review_context.json", "--root", "."]
    if a.dry_run:
        disp.append("--dry-run")
    rc = schritt(2, "an den Reviewer geben", disp)
    if rc != 0:
        print(f"\nAbbruch: kein Urteil geholt (Exit {rc}). Das Paket liegt in {a.out}/ "
              "und kann von Hand uebergeben werden.", file=sys.stderr)
        return rc
    if a.dry_run:
        print("\ndry-run: nichts versendet, kein Gate gerechnet.")
        return 0

    rc = schritt(3, "Gate rechnen", [sys.executable, str(HERE / "review_result.py"),
                                     f"{a.out}/review_result.json",
                                     "--context", f"{a.out}/review_context.json",
                                     "--dispatch", f"{a.out}/review_dispatch.json"])
    print(f"\n{'=' * 58}\nERGEBNIS: {GATE_NAMEN.get(rc, f'Exit {rc}')}")
    print("Die Entscheidung liegt beim Menschen. Uebernommen wird von Hand.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
