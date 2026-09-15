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
                  "deliveries": "work/deliveries.jsonl", "runs": "work/runs"}

# Endliche Vorgaben. "Maximal zwei Korrekturrunden insgesamt" heisst: die
# Erstlieferung plus hoechstens zwei Nachbesserungen, und eine Nachbesserung
# nach fehlgeschlagenen Maschinentests zaehlt genauso wie eine nach einem
# Reviewer-Befund. Kein Wert hier ist unbegrenzt — ein Limit, das man weglassen
# kann, ist keins.
STANDARD_LIMITS = {
    "max_correction_rounds": 2,
    "max_wall_minutes": 180,
    "max_builder_calls": 3,
    "max_reviewer_calls": 3,
    "max_cost_usd": None,          # bindet nur mit konfigurierten Token-Preisen
    "builder_timeout_seconds": 3600,
}


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
        self.limits = {**STANDARD_LIMITS, **(roh.get("limits") or {})}
        self.builder: dict = roh.get("builder") or {}
        self.model_families: dict = roh.get("model_families") or {}
        # IDs, die dem ID-Muster entsprechen, aber in einem ANDEREN Projekt
        # definiert sind — Wert ist die Herkunft. Ohne diese Liste meldet der
        # Registerpruefer sie als toten Anker, und das waere ein Fehlalarm.
        self.external_ids: dict = roh.get("external_ids") or {}
        # Austauschbare Kontextsuche. Leer = eingebauter Index bzw. gar keiner.
        # Nichts im Verfahren haengt davon ab.
        self.graph: dict = roh.get("graph") or {}
        # Pfadmuster, die NICHT nach ID-Referenzen durchsucht werden. Gedacht
        # fuer Werkzeugverzeichnisse: eine ID in einem Testfixture ist keine
        # Referenz, und sie als toten Anker zu melden ist ein Fehlalarm.
        self.ignore_paths: list = roh.get("ignore_paths") or []
        # Das Testkommando des Projekts. Steht es hier, muss es niemand bei
        # jedem Lauf wiederholen — und es ist dieselbe Zeile fuer den
        # Bereitschaftstest, den Orchestrator und jeden Menschen, der nachsieht.
        self.tests: str | None = roh.get("tests") or None

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
