"""Modellfamilien bestimmen und die Trennung nachweisen — nicht voraussetzen.

Das Werkzeug existiert wegen einer einzigen Fehlerklasse: *eine Governance-
Maschine bestaetigt einen falschen Zustand, wenn Pruefer und Erzeuger dieselbe
Interpretationslogik teilen.* Die getrennte Modellfamilie ist die Gegenmassnahme.
Bis hierher stand sie in der Konfiguration und wurde nirgends nachgerechnet —
ein Reviewer aus der Builder-Familie erzeugte ein gruenes Gate.        (Befund G04)

Drei Regeln, und die dritte ist die wichtigste:

  1. Gleiche Zeichenkette => gleiche Familie. Immer, auch bei unbekanntem Modell.
  2. Zwei BEKANNTE, verschiedene Familien => getrennt.
  3. Ist auch nur eine Familie unbekannt, ist die Trennung NICHT NACHGEWIESEN.
     Unbekannt ist nicht dasselbe wie verschieden. Zwei Modelle, die beide
     niemand zuordnen kann, koennen dasselbe Modell hinter zwei Namen sein.

Wer ein Modell benutzt, das die Tabelle nicht kennt (eigener Endpunkt, Proxy,
Aliasname), erklaert die Familie ausdruecklich:

    DEVOS_REVIEWER_FAMILY=openai          # bzw. DEVOS_BUILDER_FAMILY
    .devos.json: {"model_families": {"mein-alias": "openai"}}

Eine Erklaerung ist eine Behauptung des Menschen, keine Messung — sie wird als
`declared_via` mitgefuehrt, damit auf dem Entscheidungsblatt steht, woher die
Zuordnung kam.
"""
from __future__ import annotations

import os
import re

# Praefixmuster je Familie. Bewusst knapp: eine Tabelle, die raet, ist
# schaedlicher als eine, die "unbekannt" sagt.
TABELLE: list[tuple[str, str]] = [
    (r"^(gpt|chatgpt|o[1-5])\b", "openai"),
    (r"^text-(davinci|embedding)", "openai"),
    (r"^claude", "anthropic"),
    (r"^gemini", "google"),
    (r"^gemma", "google"),
    (r"^grok", "xai"),
    (r"^(llama|meta-llama)", "meta"),
    (r"^(mistral|mixtral|magistral|ministral|codestral|devstral)", "mistral"),
    (r"^(qwen|qwq)", "alibaba"),
    (r"^(deepseek)", "deepseek"),
    (r"^(command|cohere)", "cohere"),
    (r"^(glm|chatglm)", "zhipu"),
    (r"^(kimi|moonshot)", "moonshot"),
    (r"^(phi-|phi\d)", "microsoft"),
    (r"^(nova)", "amazon"),
]


def normalisieren(model: str) -> str:
    """Anbieter- und Regionspraefixe abstreifen: 'us.anthropic.claude-x' -> 'claude-x'."""
    m = (model or "").strip().lower()
    m = re.sub(r"^(us|eu|apac|global)\.", "", m)
    for p in ("anthropic.", "openai/", "anthropic/", "google/", "meta-llama/", "mistralai/",
              "accounts/fireworks/models/", "models/"):
        if m.startswith(p):
            m = m[len(p):]
    return m


def familie(model: str | None, overrides: dict | None = None,
            erklaert: str | None = None) -> tuple[str | None, str]:
    """(Familie oder None, woher die Zuordnung kam)."""
    if erklaert:
        return erklaert.strip().lower(), "ausdruecklich erklaert"
    if not model:
        return None, "kein Modell genannt"
    roh = model.strip()
    for k, v in (overrides or {}).items():
        if k.strip().lower() == roh.lower():
            return str(v).strip().lower(), ".devos.json: model_families"
    m = normalisieren(roh)
    for muster, fam in TABELLE:
        if re.match(muster, m):
            return fam, "Namenstabelle"
    return None, f"Modell {roh!r} steht in keiner Tabelle und wurde nicht erklaert"


def aus_umgebung(rolle: str, overrides: dict | None = None) -> dict:
    """Modell und Familie einer Rolle ('BUILDER' | 'REVIEWER') aus der Umgebung."""
    model = os.environ.get(f"DEVOS_{rolle}_MODEL")
    erkl = os.environ.get(f"DEVOS_{rolle}_FAMILY")
    fam, woher = familie(model, overrides, erkl)
    return {"model": model, "family": fam,
            "declared_via": woher if model or erkl else None}


def trennung(builder: dict | None, reviewer: dict | None) -> dict:
    """Der Nachweis — `separated` ist dreiwertig: True, False, None.

    None heisst NICHT NACHGEWIESEN und ist ausdruecklich kein Zwischending
    zwischen wahr und falsch. Es heisst: hier fehlt der Beleg, entscheide du.
    """
    b, r = builder or {}, reviewer or {}
    bm, rm = (b.get("model") or "").strip(), (r.get("model") or "").strip()
    bf, rf = b.get("family"), r.get("family")

    if bm and rm and bm.lower() == rm.lower():
        return {"separated": False, "builder_family": bf, "reviewer_family": rf,
                "why": f"Builder und Reviewer sind dasselbe Modell {bm!r} — "
                       "ein Modell, das sich selbst prueft, teilt seine Interpretationslogik "
                       "vollstaendig mit sich selbst"}
    if bf and rf and bf == rf:
        return {"separated": False, "builder_family": bf, "reviewer_family": rf,
                "why": f"Builder ({bm or '—'}) und Reviewer ({rm or '—'}) gehoeren zur "
                       f"selben Modellfamilie {bf!r}"}
    if bf and rf:
        return {"separated": True, "builder_family": bf, "reviewer_family": rf,
                "why": f"Builder-Familie {bf!r}, Reviewer-Familie {rf!r} — verschieden"}

    fehlt = []
    if not bf:
        fehlt.append(f"Builder ({b.get('declared_via') or 'DEVOS_BUILDER_MODEL nicht gesetzt'})")
    if not rf:
        fehlt.append(f"Reviewer ({r.get('declared_via') or 'DEVOS_REVIEWER_MODEL nicht gesetzt'})")
    return {"separated": None, "builder_family": bf, "reviewer_family": rf,
            "why": "Familientrennung nicht nachgewiesen — unbestimmte Familie bei: "
                   + ", ".join(fehlt)
                   + ". Unbekannt ist nicht verschieden: zwei nicht zuordenbare Namen "
                     "koennen dasselbe Modell sein."}
