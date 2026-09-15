# TASK-001 — Die Provenienz wird geprüft, nicht geglaubt

**Schiene:** Pilot · **Geschätzt:** 1 – 2 h · **Branch:** `claude/task-001-provenienz-schema`

## Gegenstand

Das Gate weist seit G04 ein `PASS` zurück, wenn die Trennung der Modellfamilien nicht nachgewiesen
ist. Den Nachweis liest es aus `review_dispatch.json`. Diese Datei wird selbst **nicht geprüft** —
weder gegen ein Schema noch auf Plausibilität.

Vorgeführt am Stand `65e869e`: eine von Hand geschriebene Datei mit dem vollständigen Inhalt

```json
{"reviewer": {"model": "gpt-5", "family": "openai"}}
```

— ohne `ok`, ohne `attempts`, ohne `request_sha256`, ohne Endpunkt, ohne Zeitstempel — wird vom Gate
als *„Transport-Provenienz (gemessen)"* geführt und erzeugt **Exit 0, `MERGEABLE_PENDING_HUMAN`**.

Das ist dieselbe Fehlerklasse wie G01 und G04: eine Datei, die als JSON parst, gilt als Beleg. Der
Unterschied zu G01 ist, dass es diesmal um den Beleg der **Gegenmaßnahme selbst** geht — die einzige
Zusicherung, auf der das ganze Verfahren ruht.

Danach ist möglich, was jetzt nicht möglich ist: das Gate unterscheidet einen *gemessenen* Nachweis
von einer *Behauptung* über einen Nachweis.

## Acceptance

- `schema/review_dispatch.schema.json` existiert und beschreibt, was `review_dispatch.py` schreibt
- `python3 tools/reproduce_findings.py` kennt einen Fall `G06`, der die oben gezeigte Datei benutzt,
  und meldet ihn als behoben
- eine Provenienz, die das Schema verletzt, zählt **nicht** als gemessener Nachweis: das Gate fällt
  auf die Selbstauskunft des Reviewers zurück oder auf „kein Nachweis", und sagt im Ergebnis, warum
- eine Provenienz mit `"ok": false` oder ohne erfolgreichen Versuch in `attempts` zählt ebenfalls
  nicht als gemessener Nachweis
- die oben gezeigte dreizeilige Datei erzeugt kein Exit 0 mehr
- `python3 tools/selftest.py` endet mit Exit 0, und keine der 121 vorhandenen Proben wurde
  abgeschwächt, um das zu erreichen

## Forbidden

- keine neue Abhängigkeit außerhalb der Standardbibliothek
- keine Verschiebung der Programmgrenzen: `review_dispatch.py` rechnet weiterhin kein Gate, und
  `review_result.py` versendet weiterhin nichts
- keine vorhandene Probe entschärfen, umbenennen oder entfernen, damit eine neue besteht
- kein neues Konfigurationsschalterwerk; die Prüfung ist an oder das Gate ist keins

## Relevante Beschlüsse

- G01 — die Begründung dafür, dass eine parsende JSON-Datei kein Beleg ist
- G04 — warum die Familientrennung überhaupt nachgewiesen werden muss

## Offen / Unentschieden

Der Builder entscheidet das **nicht** selbst, sondern legt es dem Menschen vor:

- Soll `request_sha256` aus der Provenienz gegen die tatsächliche `REVIEW-REQUEST.md` geprüft werden?
  Das würde den Nachweis an *dieses* Paket binden statt nur an *ein* Paket — es ist die eigentliche
  Lücke dahinter, aber es ist eine größere Änderung als diese Task.
- Eine Provenienz aus einem älteren Werkzeugstand (`schema_version: "1.0"`) hat kein `reviewer`-Feld.
  Gilt sie als „kein Nachweis" (dann blockiert sie ältere Läufe) oder als Selbstauskunft?
