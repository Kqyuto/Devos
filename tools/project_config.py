"""Projektbindung des Werkzeugs — alles Projektspezifische steht im Projekt.

Das Werkzeug kennt kein Register, keinen Pfad und keine ID-Form. Es liest
`.devos.json` aus der Wurzel des geprueften Projekts. Fehlt sie, arbeitet es
weiter — aber ohne Normquellen, und es sagt das in `omitted`, statt so zu tun,
als haette es nichts zu holen gegeben.

    {
      "schema_version": "1.0",
      "project": "mahoraga",
      "id_pattern": "\\\\b(D-\\\\d{3}|OQ-\\\\d{3})\\\\b",
      "registers": {
        "D":  {"path": "workshop/registers/decision-register.md", "kind": "heading",
               "heading_pattern": "^#{1,4}\\\\s+D-\\\\d{3}\\\\b"},
        "OQ": {"path": "workshop/registers/open-questions.md", "kind": "row"}
      },
      "paths": {"tasks": "work/tasks", "review": "work/review",
                "deliveries": "work/deliveries.jsonl"}
    }

`kind` ist "heading" (Abschnitt bis zur naechsten gleichrangigen Ueberschrift)
oder "row" (Tabellenzeilen, die mit `| <ID> ` beginnen).
"""
from __future__ import annotations

import json
from pathlib import Path

DATEI = ".devos.json"
STANDARD_ID_PATTERN = r"\b([A-Z]{1,4}-\d{1,4})\b"
STANDARD_PFADE = {"tasks": "work/tasks", "review": "work/review",
                  "deliveries": "work/deliveries.jsonl"}


class Config:
    def __init__(self, root: str):
        self.root = Path(root)
        self.pfad = self.root / DATEI
        self.vorhanden = self.pfad.exists()
        self.fehler: str | None = None
        roh: dict = {}
        if self.vorhanden:
            try:
                roh = json.loads(self.pfad.read_text(encoding="utf-8"))
            except Exception as ex:
                self.fehler = f"{DATEI} nicht lesbar: {ex}"
                roh = {}
        self.project = roh.get("project") or self.root.name
        self.id_pattern = roh.get("id_pattern") or STANDARD_ID_PATTERN
        self.registers: dict[str, dict] = roh.get("registers") or {}
        self.paths = {**STANDARD_PFADE, **(roh.get("paths") or {})}

    def hinweis(self) -> dict | None:
        """Der Eintrag fuer `omitted`, wenn die Bindung fehlt oder kaputt ist."""
        if self.fehler:
            return {"path": DATEI, "why": self.fehler +
                    " — es wurden keine Normquellen mitgeliefert"}
        if not self.vorhanden:
            return {"path": DATEI, "why": f"keine {DATEI} in {self.root} — das Werkzeug kennt die "
                    "Register dieses Projekts nicht und liefert keine Normquellen mit"}
        if not self.registers:
            return {"path": DATEI, "why": f"{DATEI} nennt keine `registers` — keine Normquellen"}
        return None
