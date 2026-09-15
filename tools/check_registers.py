#!/usr/bin/env python3
"""Prueft die Registerintegritaet eines Projekts — projektunabhaengig, ueber `.devos.json`.

    python3 devos/tools/check_registers.py --root .          # Exit 1 bei jedem Verstoss

Warum das hier liegt und nicht im geprueften Projekt: es ist keine Domaenenlogik.
Was eine ID ist, wo sie definiert wird und in welcher Form, steht bereits in
`.devos.json` — dem Vertrag, den DevOS ohnehin liest. Ein Projekt, das diesen
Vertrag erfuellt, bekommt seinen Registerpruefer damit geschenkt.

Das loest zugleich ein Henne-Ei-Problem: ein Repo ohne ausfuehrbare Zeile hat
kein Testkommando, und ohne Testkommando ist `tests.ran = false` — das Gate
schliesst `PASS` aus, und JEDE Lieferung endet bei CHANGES_REQUIRED, unabhaengig
vom Inhalt. Ein reines Dokumenten-Repo kann ohne so etwas nicht am Verfahren
teilnehmen.

Geprueft wird:

  1. Kein toter Anker — jede referenzierte ID ist in ihrem Register definiert.
  2. Keine Doppeldefinition — keine ID zweimal in derselben Fassung.
  3. Registerform — Zeilenregister haben die Spaltenzahl ihrer Kopfzeile.
  4. Jede referenzierte ID hat ein Register in `.devos.json`.

Drei Dinge sind ausdruecklich KEIN Fehler, weil ein Pruefer, der Fehlalarme
erzeugt, schlimmer ist als keiner:

  * **Nie referenzierte IDs.** Eine beschlossene, noch nirgends benutzte
    Entscheidung ist zulaessig. Hinweis, kein Befund.
  * **Mehrfassungen.** `## D-006 — …` und `## D-006 Rev. 2 — …` sind zwei
    Fassungen derselben Entscheidung, keine Doppeldefinition. Erkannt am
    Revisionsmarker; welche Ueberschriften so gewertet wurden, steht im Bericht.
    Nur zwei Ueberschriften OHNE Revisionsmarker sind ein Befund.
  * **Fremde IDs.** `R-213` stammt aus dem Assay und nicht aus dem Risk Register
    dieses Repos — das ID-Muster kann das nicht wissen. Solche IDs stehen mit
    Herkunft in `.devos.json` unter `external_ids` und werden als *auswaertig
    definiert* gefuehrt, nicht als toter Anker.

Was eine ID ist, entscheidet ausschliesslich `id_pattern` aus `.devos.json`.
Dieser Pruefer bringt keine zweite ID-Grammatik mit — eine zweite Grammatik
waere eine zweite Wahrheit.

Exit 0 sauber · 1 mindestens ein Verstoss · 2 Konfiguration unbrauchbar.
Kein Feld `bestanden: false` bei fortlaufendem Programm — eine Probe ohne
Abbruchwirkung ist eine Anzeige, kein Gate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_config import Config      # noqa: E402

UEBERSPRINGEN = {".git", "__pycache__", "node_modules", ".venv"}


def praefix(rid: str) -> str:
    m = re.match(r"^[A-Za-z]+", rid)
    return m.group(0) if m else rid


STANDARD_REVISION = r"(?i)^\s*(rev\.?|revision|fassung|v)\s*\.?\s*\d+"


def ist_id(token: str, cfg: Config) -> bool:
    """Genau die IDs, die `id_pattern` des Projekts meint — keine zweite Grammatik."""
    try:
        return re.fullmatch(cfg.id_pattern, token) is not None
    except re.error:
        return False


def definitionen(root: Path, cfg: Config) -> tuple[dict, list[str], list[str]]:
    """ID -> [(Pfad, Zeile, ist_revision)], Formfehler, Revisionshinweise."""
    gefunden: dict[str, list[tuple[str, int, bool]]] = {}
    formfehler: list[str] = []
    revisionen: list[str] = []
    for pre, spec in cfg.registers.items():
        rel = spec.get("path")
        f = root / rel if rel else None
        if not f or not f.exists():
            formfehler.append(f"Register {pre!r}: {rel} existiert nicht")
            continue
        rev_muster = spec.get("revision_marker", STANDARD_REVISION)
        zeilen = f.read_text(encoding="utf-8").splitlines()
        if spec.get("kind", "row") == "heading":
            for i, l in enumerate(zeilen, 1):
                m = re.match(r"^#{1,6}\s+(\S+)\s*(.*)$", l)
                if not m or not ist_id(m.group(1), cfg) or praefix(m.group(1)) != pre:
                    continue
                rest = m.group(2).strip()
                rev = bool(re.match(rev_muster, rest))
                if rev:
                    revisionen.append(f"{rel}:{i}: {m.group(1)} — Fassung {rest[:40]!r}")
                gefunden.setdefault(m.group(1), []).append((rel, i, rev))
                if not rest.lstrip("—- "):
                    formfehler.append(f"{rel}:{i}: Abschnitt {m.group(1)} hat keinen Titel")
        else:
            spalten = None
            for i, l in enumerate(zeilen, 1):
                z = l.strip()
                if not z.startswith("|"):
                    continue
                felder = z.split("|")[1:-1] if z.endswith("|") else z.split("|")[1:]
                if set(z.replace("|", "").replace(" ", "")) <= set("-:") and z.count("|") > 1:
                    continue                      # Trennzeile der Tabelle
                if spalten is None:
                    spalten = len(felder)
                    continue                      # Kopfzeile
                erste = felder[0].strip() if felder else ""
                if not ist_id(erste, cfg) or praefix(erste) != pre:
                    continue
                gefunden.setdefault(erste, []).append((rel, i, False))
                if len(felder) != spalten:
                    formfehler.append(f"{rel}:{i}: Zeile {erste} hat {len(felder)} Spalten, "
                                      f"die Kopfzeile {spalten}")
    return gefunden, formfehler, revisionen


def referenzen(root: Path, cfg: Config) -> dict[str, list[tuple[str, int]]]:
    """ID -> [(Pfad, Zeile)] ueber alle Textdateien des Projekts."""
    muster = re.compile(cfg.id_pattern)
    out: dict[str, list[tuple[str, int]]] = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or any(t in f.parts for t in UEBERSPRINGEN):
            continue
        if f.suffix.lower() not in (".md", ".txt", ".json", ".yaml", ".yml", ".py", ".rst"):
            continue
        rel = str(f.relative_to(root))
        try:
            zeilen = f.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, l in enumerate(zeilen, 1):
            for m in muster.finditer(l):
                rid = m.group(1) if m.groups() else m.group(0)
                out.setdefault(rid, []).append((rel, i))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Registerintegritaet gegen .devos.json pruefen")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    cfg = Config(str(root))
    if cfg.fehler:
        print(f"[2] {cfg.fehler}", file=sys.stderr)
        return 2
    if not cfg.registers:
        print("[2] .devos.json nennt keine `registers` — es gibt nichts zu pruefen.\n"
              "    Das ist keine bestandene Pruefung, sondern eine fehlende Konfiguration.",
              file=sys.stderr)
        return 2

    defs, formfehler, revisionen = definitionen(root, cfg)
    refs = referenzen(root, cfg)
    extern = cfg.external_ids

    # Mehrere Ueberschriften zu einer ID sind nur dann ein Befund, wenn KEINE
    # davon als Fassung ausgewiesen ist. Sonst ist es eine Revisionskette.
    doppelt = {k: v for k, v in defs.items()
               if len(v) > 1 and not any(rev for _, _, rev in v)}
    mehrfassung = {k: v for k, v in defs.items()
                   if len(v) > 1 and any(rev for _, _, rev in v)}
    tot = {k: v for k, v in refs.items() if k not in defs and k not in extern}
    auswaertig = {k: v for k, v in refs.items() if k not in defs and k in extern}
    ohne_register = {k for k in tot if praefix(k) not in cfg.registers}
    nie = sorted(set(defs) - set(refs))

    befunde: list[str] = []
    for rid, orte in sorted(doppelt.items()):
        befunde.append(f"Doppeldefinition {rid}: "
                       + ", ".join(f"{p}:{z}" for p, z, _ in orte)
                       + " — keine der Fassungen traegt einen Revisionsmarker")
    for rid, orte in sorted(tot.items()):
        wo = ", ".join(f"{p}:{z}" for p, z in orte[:3])
        mehr = f" (+{len(orte) - 3} weitere)" if len(orte) > 3 else ""
        grund = ("kein Register fuer Praefix " + repr(praefix(rid))
                 if rid in ohne_register else "nirgends definiert")
        befunde.append(f"Toter Anker {rid}: {grund} — referenziert in {wo}{mehr}. "
                       "Stammt die ID aus einem anderen Projekt, gehoert sie mit Herkunft "
                       "nach .devos.json unter `external_ids`")
    befunde += [f"Registerform: {x}" for x in formfehler]

    if a.json:
        print(json.dumps({"ok": not befunde, "befunde": befunde,
                          "definiert": len(defs), "referenziert": len(refs),
                          "nie_referenziert": nie,
                          "mehrfassungen": sorted(mehrfassung),
                          "auswaertig": sorted(auswaertig)}, ensure_ascii=False, indent=2))
        return 1 if befunde else 0

    print(f"Registerpruefung · {cfg.project} · {len(cfg.registers)} Register")
    print(f"  definierte IDs   : {len(defs)}")
    print(f"  referenzierte IDs: {len(refs)}")
    if nie:
        print(f"  nie referenziert : {len(nie)} — zulaessig: "
              f"{', '.join(nie[:8])}{' …' if len(nie) > 8 else ''}")
    if mehrfassung:
        print(f"  Mehrfassungen    : {len(mehrfassung)} — zulaessig, als Revision erkannt: "
              f"{', '.join(sorted(mehrfassung)[:8])}{' …' if len(mehrfassung) > 8 else ''}")
    if auswaertig:
        print(f"  auswaertig       : {len(auswaertig)} — laut .devos.json fremd definiert: "
              + ", ".join(f"{k} ({extern[k][:40]})" for k in sorted(auswaertig)[:4]))
    if not befunde:
        print("\nverdict: ok — kein toter Anker, keine Doppeldefinition, Registerform sauber.")
        return 0
    print(f"\n{len(befunde)} BEFUND(E):")
    for b in befunde:
        print(f"  - {b}")
    print("\nverdict: FEHLGESCHLAGEN")
    return 1


if __name__ == "__main__":
    sys.exit(main())
