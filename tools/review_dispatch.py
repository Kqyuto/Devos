#!/usr/bin/env python3
"""Uebergibt das Review-Paket an den Reviewer und holt sein Urteil zurueck.

Das ist der Schritt, der den Menschen aus der Transportrolle nimmt. Bis hierher
musste er das Paket kopieren und die Antwort zurueckbringen; ab hier ist das ein
Kommando.

Was dieses Skript ausdruecklich NICHT tut:

  * Es urteilt nicht. Es transportiert. Das Gate ist ein eigenes Skript und
    bleibt es, damit der Versender kein PASS erzeugen kann.
  * Es erfindet nichts. Antwortet das Modell kein schemagueltiges JSON, gibt es
    EINEN Reparaturversuch mit den Schemafehlern im Klartext - danach bricht es
    ab und legt die Rohantwort daneben. Kein Schleifendrehen, bis etwas parst.
  * Es schreibt `review_result.json` erst, wenn die Antwort das Schema besteht.
    Eine halb gueltige Datei waere schlimmer als keine.
  * Es schreibt den Schluessel nirgends hin und gibt ihn nirgends aus.

Konfiguration ueber Umgebungsvariablen - der Schluessel gehoert nicht ins Repo:

    DEVOS_REVIEWER_API_KEY    Pflicht
    DEVOS_REVIEWER_BASE_URL   Standard https://api.openai.com/v1
    DEVOS_REVIEWER_MODEL      Pflicht, z. B. gpt-5
    DEVOS_REVIEWER_TIMEOUT    Sekunden, Standard 600

Jede OpenAI-kompatible Chat-Completions-Schnittstelle funktioniert. Die
Familientrennung nach V-DEV-2 ist eine Konfigurationsfrage, keine Codefrage -
und sie wird in der Provenienz festgehalten, damit sie pruefbar bleibt.

Exit: 0 Urteil geholt und schemagueltig · 2 Konfiguration fehlt
      4 Antwort nicht schemagueltig (Rohantwort liegt daneben) · 5 Transportfehler
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonschema_mini as J  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "review_result.schema.json"

ANWEISUNG = """Du bist ADVERSARIAL REVIEWER in einem Zwei-Modell-Verfahren.

Deine Rolle ist scharf begrenzt:
- Du urteilst. Du gibst nichts frei und du aenderst nichts.
- Du bist NICHT der Erzeuger dieser Lieferung. Deine Aufgabe ist es, Fehler zu
  finden, die der Erzeuger nicht finden konnte, weil er seine eigene
  Interpretationslogik teilt.
- Reicht das Paket nicht zum Urteilen, ist die richtige Antwort
  INSUFFICIENT_CONTEXT und ausdruecklich NICHT PASS. Ein selbstbewusstes PASS auf
  unvollstaendiger Grundlage ist der teuerste Fehler, den du machen kannst.
- Jeder blockierende Befund braucht eine Fundstelle (Datei und Zeile oder
  Abschnitt). Ein Befund ohne Fundstelle ist kein Befund.
- Traegt die Lieferung Behauptungen, die das Paket nicht belegt, gehoeren sie
  nach unproven_claims - auch wenn sie plausibel klingen.

Antworte mit GENAU EINEM JSON-Objekt nach dem folgenden Schema. Kein Fliesstext
davor oder danach, keine Code-Fences.

reviewed_range.base und reviewed_range.head MUESSEN exakt die vollen SHAs aus
dem Paketkopf sein. Das Gate vergleicht zeichengenau und verwirft alles andere.

SCHEMA:
%s
"""


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def extract_json(text: str) -> tuple[dict | None, str]:
    """Ein Objekt aus der Antwort ziehen - auch wenn Fences drum stehen."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j <= i:
        return None, "keine geschweiften Klammern in der Antwort gefunden"
    try:
        return json.loads(t[i:j + 1]), ""
    except json.JSONDecodeError as ex:
        return None, f"kein gueltiges JSON: {ex}"


def call(base: str, key: str, model: str, messages: list[dict], timeout: int) -> str:
    body = json.dumps({"model": model, "messages": messages,
                       "response_format": {"type": "json_object"}}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"[5] Reviewer-Endpunkt antwortete HTTP {ex.code}: {detail}")
    except Exception as ex:
        raise SystemExit(f"[5] Transportfehler: {type(ex).__name__}: {ex}")
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise SystemExit(f"[5] unerwartete Antwortform: {json.dumps(payload)[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Review-Paket an den Reviewer geben und Urteil holen")
    ap.add_argument("--request", default="devos/review/REVIEW-REQUEST.md")
    ap.add_argument("--out", default="devos/review/review_result.json")
    ap.add_argument("--dry-run", action="store_true", help="Anfrage schreiben, nicht senden")
    a = ap.parse_args()

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    req_path = Path(a.request)
    if not req_path.exists():
        print(f"[2] Review-Paket fehlt: {req_path}", file=sys.stderr)
        return 2
    paket = req_path.read_text(encoding="utf-8")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    messages = [{"role": "system", "content": ANWEISUNG % json.dumps(schema, ensure_ascii=False, indent=2)},
                {"role": "user", "content": paket}]

    if a.dry_run:
        p = out.parent / "review_request_payload.json"
        p.write_text(json.dumps({"messages": messages}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"dry-run: Anfrage geschrieben nach {p} ({len(paket)} B Paket) — nichts gesendet")
        return 0

    key = os.environ.get("DEVOS_REVIEWER_API_KEY")
    model = os.environ.get("DEVOS_REVIEWER_MODEL")
    base = os.environ.get("DEVOS_REVIEWER_BASE_URL", "https://api.openai.com/v1")
    timeout = int(os.environ.get("DEVOS_REVIEWER_TIMEOUT", "600"))
    if not key or not model:
        print("[2] DEVOS_REVIEWER_API_KEY und DEVOS_REVIEWER_MODEL muessen gesetzt sein.\n"
              "    Der Schluessel gehoert in die Umgebung, nicht ins Repo.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    versuche = []
    result = None
    for runde in (1, 2):
        roh = call(base, key, model, messages, timeout)
        obj, warum = extract_json(roh)
        fehler = J.validate(obj, schema) if obj is not None else [warum]
        versuche.append({"runde": runde, "response_sha256": sha(roh), "bytes": len(roh.encode()),
                         "schema_errors": fehler})
        if not fehler:
            result = obj
            break
        (out.parent / f"review_result_raw_r{runde}.txt").write_text(roh, encoding="utf-8")
        print(f"Runde {runde}: Antwort nicht schemagueltig ({len(fehler)} Fehler)", file=sys.stderr)
        for f in fehler[:8]:
            print(f"    - {f}", file=sys.stderr)
        if runde == 2:
            break
        messages += [{"role": "assistant", "content": roh},
                     {"role": "user", "content": "Deine Antwort verletzt das Schema:\n- "
                                                 + "\n- ".join(fehler)
                                                 + "\n\nAntworte erneut mit GENAU EINEM gueltigen "
                                                   "JSON-Objekt. Aendere dein Urteil nicht, "
                                                   "nur die Form."}]

    prov = {"schema_version": "1.0", "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "endpoint_host": urlsplit(base).netloc, "model": model,
            "request_sha256": sha(paket), "request_bytes": len(paket.encode()),
            "attempts": versuche, "ok": result is not None}
    (out.parent / "review_dispatch.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")

    if result is None:
        print("[4] Kein schemagueltiges Urteil erhalten. Rohantworten liegen daneben.\n"
              "    Es wurde NICHTS nach review_result.json geschrieben — eine halb gueltige\n"
              "    Datei waere schlimmer als keine.", file=sys.stderr)
        return 4

    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Urteil geholt: {result['status']} · {len(result['blocking'])} blockierend · "
          f"Modell {model} @ {prov['endpoint_host']} · Runden {len(versuche)}")
    print(f"geschrieben: {out}\ngeschrieben: {out.parent/'review_dispatch.json'}")
    print("\nDas Gate ist ein eigener Schritt — dieses Skript urteilt nicht:")
    print(f"    python3 devos/tools/review_result.py {out} --context {out.parent/'review_context.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as ex:
        if isinstance(ex.code, str) and ex.code.startswith("[5]"):
            print(ex.code, file=sys.stderr)
            sys.exit(5)
        raise
