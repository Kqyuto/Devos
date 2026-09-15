"""Laedt Zugangsdaten aus einer Datei AUSSERHALB beider Repositories.

    ~/.config/devos/env        (oder $DEVOS_ENV_FILE)

Warum nicht `.env` im Repo: ein Schluessel im Arbeitsverzeichnis wird irgendwann
mitcommittet. Nicht aus Nachlaessigkeit, sondern weil `git add -A` genau dafuer
gebaut ist. Die Datei liegt deshalb im Benutzerprofil und wird von keinem der
beiden Repos gesehen.

Bereits gesetzte Umgebungsvariablen werden NICHT ueberschrieben. Die Umgebung
gewinnt immer gegen die Datei — sonst kann man einen Lauf nicht mehr gezielt
anders konfigurieren, und ein Test nicht mehr von seiner eigenen Umgebung
ausgehen.
"""
from __future__ import annotations

import os
from pathlib import Path

STANDARD = Path.home() / ".config" / "devos" / "env"


def pfad() -> Path:
    return Path(os.environ["DEVOS_ENV_FILE"]) if os.environ.get("DEVOS_ENV_FILE") else STANDARD


def laden() -> dict:
    """(geladen, Pfad, Anzahl, Warnungen) — gibt nie einen Wert aus."""
    p = pfad()
    bericht = {"loaded": False, "path": str(p), "set": [], "present": [], "warnings": []}
    if not p.exists():
        return bericht
    try:
        roh = p.read_text(encoding="utf-8")
    except OSError as ex:
        bericht["warnings"].append(f"{p} nicht lesbar: {ex}")
        return bericht

    try:
        modus = p.stat().st_mode & 0o077
        if modus:
            bericht["warnings"].append(
                f"{p} ist fuer andere Benutzer lesbar (Rechte {oct(p.stat().st_mode & 0o777)}) — "
                "`chmod 600` waere richtig")
    except OSError:
        pass

    for i, zeile in enumerate(roh.splitlines(), 1):
        z = zeile.strip()
        if not z or z.startswith("#"):
            continue
        if z.startswith("export "):
            z = z[len("export "):].lstrip()
        if "=" not in z:
            bericht["warnings"].append(f"{p}:{i}: keine KEY=WERT-Zeile — uebersprungen")
            continue
        k, v = z.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if not k:
            continue
        bericht["present"].append(k)
        if k in os.environ:
            continue                 # die Umgebung gewinnt
        os.environ[k] = v
        bericht["set"].append(k)     # nur der NAME, nie der Wert
    bericht["loaded"] = True
    return bericht
