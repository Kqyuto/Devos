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

Zwei Dinge, die es seit Befund G04 und Schritt 3 zusaetzlich tut:

  * Es VERWEIGERT den Versand, wenn Reviewer und Builder derselben Modellfamilie
    angehoeren. Vorher war die Familientrennung eine Konfigurationsfrage, die
    niemand nachrechnete - ein Modell durfte sich selbst pruefen und erzeugte ein
    gruenes Gate. Jetzt scheitert das, bevor Aufwand entsteht.            (G04)
  * Es gibt dem Reviewer EINE begrenzte Nachforderung auf Originalquellen. Der
    Reviewer, der nur sieht, was der Builder ausgewaehlt hat, prueft die Auswahl
    des Builders. Angeforderte Pfade werden aus der GEPRUEFTEN Revision gelesen
    (`git show <head>:<pfad>`), nie aus dem Arbeitsverzeichnis, und jede
    Nachforderung steht in der Provenienz.

Konfiguration ueber Umgebungsvariablen - der Schluessel gehoert nicht ins Repo:

    DEVOS_REVIEWER_API_KEY    Pflicht
    DEVOS_REVIEWER_BASE_URL   Standard https://api.openai.com/v1
    DEVOS_REVIEWER_MODEL      Pflicht, z. B. gpt-5
    DEVOS_REVIEWER_FAMILY     nur noetig, wenn der Modellname unbekannt ist
    DEVOS_BUILDER_MODEL       Pflicht fuer den Nachweis der Familientrennung
    DEVOS_BUILDER_FAMILY      wie oben
    DEVOS_REVIEWER_TIMEOUT    Sekunden, Standard 600
    DEVOS_REVIEWER_PRICE_IN   USD je 1 Mio. Eingabe-Token, optional
    DEVOS_REVIEWER_PRICE_OUT  USD je 1 Mio. Ausgabe-Token, optional

Jede OpenAI-kompatible Chat-Completions-Schnittstelle funktioniert.

Exit: 0 Urteil geholt und schemagueltig · 2 Konfiguration fehlt oder
      Familientrennung verletzt · 4 Antwort nicht schemagueltig (Rohantwort
      liegt daneben) · 5 Transportfehler
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jsonschema_mini as J          # noqa: E402
import model_family as MF            # noqa: E402
import devos_env                     # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "review_result.schema.json"

MAX_AUFRUFE = 4               # harte Obergrenze: keine unbegrenzten Wiederholungen
MAX_QUELLDATEIEN = 10
MAX_QUELLE_BYTES = 60_000
MAX_QUELLEN_GESAMT = 200_000

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

DU DARFST QUELLEN NACHFORDERN. Du bist nicht auf die Auswahl des Builders
beschraenkt. Der Abschnitt "Bestand im geprueften Stand" listet, was es im Repo
ueberhaupt gibt. Brauchst du eine Datei im Wortlaut, antworte mit
status INSUFFICIENT_CONTEXT und trage die Pfade EINZELN in missing_context ein -
einen Pfad je Eintrag, genau so geschrieben wie in der Bestandsliste. Du
bekommst sie dann aus der geprueften Revision und urteilst erneut. Das geht
GENAU EINMAL; danach zaehlt dein Urteil.

reviewed.acceptance_items MUSS fuer JEDEN Acceptance-Punkt des Pakets genau
einen Eintrag enthalten, und `item` MUSS der Wortlaut dieses Punktes sein.
Das Gate gleicht die Abdeckung ab: ein nicht bewerteter Punkt und eine
Bewertung, die zu keinem Punkt gehoert, verhindern beide ein PASS.

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


def call(base: str, key: str, model: str, messages: list[dict], timeout: int) -> tuple[str, dict]:
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
        inhalt = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise SystemExit(f"[5] unerwartete Antwortform: {json.dumps(payload)[:300]}")
    u = payload.get("usage") or {}
    return inhalt, {"prompt_tokens": u.get("prompt_tokens"),
                    "completion_tokens": u.get("completion_tokens"),
                    "total_tokens": u.get("total_tokens")}


def kosten(verbrauch: list[dict]) -> tuple[float | None, str]:
    """USD - oder None und der Grund. Ein geratener Preis ist keine Messung."""
    pin, pout = os.environ.get("DEVOS_REVIEWER_PRICE_IN"), os.environ.get("DEVOS_REVIEWER_PRICE_OUT")
    if not pin or not pout:
        return None, ("DEVOS_REVIEWER_PRICE_IN/OUT nicht gesetzt — Token werden gezaehlt, "
                      "Kosten nicht berechnet")
    try:
        pi, po = float(pin), float(pout)
    except ValueError:
        return None, "DEVOS_REVIEWER_PRICE_IN/OUT sind keine Zahlen"
    if any(v.get("prompt_tokens") is None for v in verbrauch):
        return None, "der Endpunkt hat keine Token-Zahlen geliefert"
    s = sum((v["prompt_tokens"] or 0) * pi + (v.get("completion_tokens") or 0) * po
            for v in verbrauch) / 1_000_000
    return round(s, 6), "aus Token-Zahlen des Endpunkts und den konfigurierten Preisen"


def quellen_holen(root: str, head: str, gewuenscht: list[str]) -> tuple[list[dict], str]:
    """Angeforderte Pfade aus der GEPRUEFTEN Revision lesen. Nie aus dem Arbeitsverzeichnis.

    Das Arbeitsverzeichnis kann Reparaturcode enthalten, der im geprueften Stand
    nicht existiert. Eine Quelle, die der Reviewer daraus lesen wuerde, gehoerte
    nicht zur Lieferung.
    """
    r = subprocess.run(["git", "-C", root, "ls-tree", "-r", "--name-only", head],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return [], f"Bestand nicht lesbar: {r.stderr.strip()[:200]}"
    bestand = r.stdout.splitlines()
    protokoll, gesamt = [], 0
    for roh in gewuenscht[:MAX_QUELLDATEIEN]:
        wunsch = (roh or "").strip().strip("`'\"").lstrip("./")
        if not wunsch:
            continue
        treffer = [p for p in bestand if p == wunsch] or \
                  [p for p in bestand if p.endswith("/" + wunsch)] or \
                  [p for p in bestand if wunsch in p]
        if len(treffer) != 1:
            protokoll.append({"asked": roh, "resolved": None,
                              "refused_why": "kein eindeutiger Pfad im geprueften Stand"
                                             if not treffer else
                                             f"{len(treffer)} Pfade passen — nicht eindeutig"})
            continue
        pfad = treffer[0]
        g = subprocess.run(["git", "-C", root, "show", f"{head}:{pfad}"],
                           capture_output=True, text=True)
        if g.returncode != 0:
            protokoll.append({"asked": roh, "resolved": pfad,
                              "refused_why": "nicht lesbar in dieser Revision"})
            continue
        n = len(g.stdout.encode())
        if n > MAX_QUELLE_BYTES:
            protokoll.append({"asked": roh, "resolved": pfad, "bytes": n,
                              "refused_why": f"{n} B > Limit {MAX_QUELLE_BYTES} B"})
            continue
        if gesamt + n > MAX_QUELLEN_GESAMT:
            protokoll.append({"asked": roh, "resolved": pfad, "bytes": n,
                              "refused_why": f"Gesamtlimit {MAX_QUELLEN_GESAMT} B erreicht"})
            continue
        gesamt += n
        protokoll.append({"asked": roh, "resolved": pfad, "bytes": n,
                          "sha256": sha(g.stdout), "text": g.stdout})
    return protokoll, ""


def nachreichung_text(protokoll: list[dict], head: str) -> str:
    L = [f"Du hast Quellen nachgefordert. Hier sind sie im Wortlaut, gelesen aus der "
         f"GEPRUEFTEN Revision {head} — nicht aus einem Arbeitsverzeichnis.", ""]
    for e in protokoll:
        if "text" in e:
            L += [f"### `{e['resolved']}` ({e['bytes']} B, sha256 {e['sha256']})", "",
                  "```", e["text"].rstrip(), "```", ""]
        else:
            L += [f"### `{e['asked']}` — NICHT geliefert: {e['refused_why']}", ""]
    L += ["Urteile jetzt abschliessend. Eine weitere Nachforderung gibt es nicht; "
          "was weiterhin fehlt, gehoert in missing_context und macht dein Urteil "
          "INSUFFICIENT_CONTEXT.", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Review-Paket an den Reviewer geben und Urteil holen")
    ap.add_argument("--request", default="devos/review/REVIEW-REQUEST.md")
    ap.add_argument("--out", default="devos/review/review_result.json")
    ap.add_argument("--context", default=None,
                    help="review_context.json — erlaubt die Quellen-Nachforderung aus dem geprueften Stand")
    ap.add_argument("--root", default=".", help="Wurzel des geprueften Projekts")
    ap.add_argument("--dry-run", action="store_true", help="Anfrage schreiben, nicht senden")
    a = ap.parse_args()

    devos_env.laden()
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

    # --- Kontext (optional) fuer Kopf, Familienangabe des Builders und Nachforderung
    ctx = None
    if a.context and Path(a.context).exists():
        try:
            geladen = json.loads(Path(a.context).read_text(encoding="utf-8"))
            ctx = geladen if isinstance(geladen, dict) else None
        except Exception:
            ctx = None

    overrides = {}
    cfgdatei = Path(a.root) / ".devos.json"
    if cfgdatei.exists():
        try:
            overrides = (json.loads(cfgdatei.read_text(encoding="utf-8")) or {}).get("model_families") or {}
        except Exception:
            overrides = {}

    reviewer = MF.aus_umgebung("REVIEWER", overrides)
    builder = (ctx or {}).get("builder") if isinstance((ctx or {}).get("builder"), dict) else None
    if not builder:
        builder = MF.aus_umgebung("BUILDER", overrides)
    unab = MF.trennung(builder, reviewer)

    # Der Versand wird verweigert, wenn die Trennung NACHWEISLICH verletzt ist.
    # Ist sie nur nicht nachgewiesen, wird gesendet — und das Gate laesst kein
    # PASS zu. Der Transport blockiert den bewiesenen Verstoss, das Gate den
    # unbewiesenen Nachweis. Beides waere an der jeweils anderen Stelle falsch.
    if unab["separated"] is False:
        print(f"[2] Versand verweigert: {unab['why']}.\n"
              "    Ein Reviewer aus der Builder-Familie teilt dessen Interpretationslogik —\n"
              "    genau die Fehlerklasse, gegen die dieses Verfahren gebaut ist.\n"
              "    Setze DEVOS_REVIEWER_MODEL auf ein Modell einer anderen Familie.",
              file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    versuche: list[dict] = []
    verbrauch: list[dict] = []
    quellen: list[dict] = []
    result = None
    formreparatur = 0
    nachgefordert = False

    while len(versuche) < MAX_AUFRUFE:
        runde = len(versuche) + 1
        roh, usage = call(base, key, model, messages, timeout)
        verbrauch.append(usage)
        obj, warum = extract_json(roh)
        fehler = J.validate(obj, schema) if obj is not None else [warum]
        versuche.append({"runde": runde, "response_sha256": sha(roh), "bytes": len(roh.encode()),
                         "schema_errors": fehler, "usage": usage})

        if fehler:
            (out.parent / f"review_result_raw_r{runde}.txt").write_text(roh, encoding="utf-8")
            print(f"Runde {runde}: Antwort nicht schemagueltig ({len(fehler)} Fehler)", file=sys.stderr)
            for f in fehler[:8]:
                print(f"    - {f}", file=sys.stderr)
            if formreparatur >= 1:
                break
            formreparatur += 1
            messages += [{"role": "assistant", "content": roh},
                         {"role": "user", "content": "Deine Antwort verletzt das Schema:\n- "
                                                     + "\n- ".join(fehler)
                                                     + "\n\nAntworte erneut mit GENAU EINEM gueltigen "
                                                       "JSON-Objekt. Aendere dein Urteil nicht, "
                                                       "nur die Form."}]
            continue

        formreparatur = 0
        head = ((ctx or {}).get("range") or {}).get("head")
        will = [x for x in (obj.get("missing_context") or []) if isinstance(x, str)]
        if (obj.get("status") == "INSUFFICIENT_CONTEXT" and will and head
                and not nachgefordert and len(versuche) < MAX_AUFRUFE):
            nachgefordert = True
            quellen, fehlgrund = quellen_holen(a.root, head, will)
            geliefert = [q for q in quellen if "text" in q]
            print(f"Runde {runde}: Reviewer fordert {len(will)} Quelle(n) nach — "
                  f"{len(geliefert)} aus {head[:12]} geliefert"
                  + (f" ({fehlgrund})" if fehlgrund else ""), file=sys.stderr)
            if quellen:
                messages += [{"role": "assistant", "content": roh},
                             {"role": "user", "content": nachreichung_text(quellen, head)}]
                continue
        result = obj
        break

    kost, kostengrund = kosten(verbrauch)
    prov = {"schema_version": "1.1", "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "endpoint_host": urlsplit(base).netloc, "model": model,
            "reviewer": reviewer, "builder": builder,
            "independence": {k: unab[k] for k in ("separated", "builder_family",
                                                  "reviewer_family", "why")},
            "request_sha256": sha(paket), "request_bytes": len(paket.encode()),
            "attempts": versuche, "calls": len(versuche),
            "usage_total": {
                "prompt_tokens": sum(v.get("prompt_tokens") or 0 for v in verbrauch) or None,
                "completion_tokens": sum(v.get("completion_tokens") or 0 for v in verbrauch) or None},
            "cost_usd": kost, "cost_basis": kostengrund,
            "source_requests": [{k: v for k, v in q.items() if k != "text"} for q in quellen],
            "ok": result is not None}
    (out.parent / "review_dispatch.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    if quellen:
        (out.parent / "review_sources.md").write_text(
            nachreichung_text(quellen, ((ctx or {}).get("range") or {}).get("head", "?")),
            encoding="utf-8")

    if result is None:
        print("[4] Kein schemagueltiges Urteil erhalten. Rohantworten liegen daneben.\n"
              "    Es wurde NICHTS nach review_result.json geschrieben — eine halb gueltige\n"
              "    Datei waere schlimmer als keine.", file=sys.stderr)
        return 4

    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    trenn = {True: "nachgewiesen", False: "VERLETZT", None: "nicht nachgewiesen"}[unab["separated"]]
    print(f"Urteil geholt: {result['status']} · {len(result['blocking'])} blockierend · "
          f"Modell {model} @ {prov['endpoint_host']} · Aufrufe {len(versuche)}")
    print(f"Familientrennung: {trenn} — {unab['why']}")
    if kost is not None:
        print(f"Kosten: {kost} USD ({kostengrund})")
    print(f"geschrieben: {out}\ngeschrieben: {out.parent/'review_dispatch.json'}")
    print("\nDas Gate ist ein eigener Schritt — dieses Skript urteilt nicht:")
    print(f"    python3 devos/tools/review_result.py {out} "
          f"--context {out.parent/'review_context.json'} "
          f"--dispatch {out.parent/'review_dispatch.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as ex:
        if isinstance(ex.code, str) and ex.code.startswith("[5]"):
            print(ex.code, file=sys.stderr)
            sys.exit(5)
        raise
