"""Minimaler JSON-Schema-Pruefer — Teilmenge, stdlib only.

Warum nicht `jsonschema`: ein Gate, das von einem Installationsschritt abhaengt,
wird in der Praxis optional, und ein optionales Gate ist keins. Geprueft werden
genau die Konstrukte, die `devos/schema/*.json` benutzt:
type · const · enum · required · properties · additionalProperties · items.
`type` darf eine Liste sein ("integer" oder "null") — mehr Kombinatorik nicht.

Alles andere im Schema wird ignoriert — und das ist der Grund, warum diese Datei
ihre eigene Grenze nennt: ein Schema-Konstrukt, das hier fehlt, wird NICHT
geprueft. Wer eines ergaenzt, ergaenzt es auch hier.

SUPPORTED nennt genau die Konstrukte, die `validate()` wirklich durchsetzt —
nicht die, die einmal gemeint waren. `allOf` und `anyOf` standen hier, ohne dass
`validate()` sie je angesehen haette: ein Schema, das sie benutzt, waere
stillschweigend ungeprueft geblieben und `unsupported_keywords()` haette
geschwiegen. Ein Pruefer, der mehr behauptet als er tut, ist die Fehlerklasse,
gegen die dieses Werkzeug gebaut ist.                                (Befund G05)
"""
from __future__ import annotations

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
         "integer": int, "number": (int, float), "null": type(None)}
SUPPORTED = {"type", "const", "enum", "required", "properties",
             "additionalProperties", "items", "description", "title", "$schema"}


def unsupported_keywords(schema: dict, path: str = "") -> list[str]:
    """Schema-Konstrukte, die dieser Pruefer NICHT durchsetzt."""
    out = []
    for k in schema:
        if k not in SUPPORTED:
            out.append(f"{path or '<root>'}: {k}")
    for k, sub in (schema.get("properties") or {}).items():
        if isinstance(sub, dict):
            out += unsupported_keywords(sub, f"{path}.{k}" if path else k)
    it = schema.get("items")
    if isinstance(it, dict):
        out += unsupported_keywords(it, f"{path}[]")
    return out


def validate(data, schema: dict, path: str = "") -> list[str]:
    e: list[str] = []
    p = path or "<root>"

    if "const" in schema and data != schema["const"]:
        e.append(f"{p}: muss {schema['const']!r} sein, ist {data!r}")
        return e

    t = schema.get("type")
    if t:
        # Eine Liste von Typen gilt als erfuellt, sobald EINER passt.
        kandidaten = t if isinstance(t, list) else [t]
        bekannt = [x for x in kandidaten if x in TYPES]
        if bekannt:
            def passt(x: str) -> bool:
                # bool ist in Python ein int - fuer JSON sind das zwei Typen
                if x in ("integer", "number") and isinstance(data, bool):
                    return False
                return isinstance(data, TYPES[x])
            if not any(passt(x) for x in bekannt):
                e.append(f"{p}: {' oder '.join(bekannt)} erwartet, "
                         f"{type(data).__name__} gefunden")
                return e

    if "enum" in schema and data not in schema["enum"]:
        e.append(f"{p}: {data!r} nicht erlaubt — zulaessig: {schema['enum']}")

    if isinstance(data, dict):
        for r in schema.get("required", []):
            if r not in data:
                e.append(f"{p}.{r}: Pflichtfeld fehlt")
        props = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            for k in data:
                if k not in props:
                    e.append(f"{p}.{k}: unbekanntes Feld")
        for k, v in data.items():
            if k in props:
                e += validate(v, props[k], f"{p}.{k}")

    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(data):
            e += validate(v, schema["items"], f"{p}[{i}]")

    return e
