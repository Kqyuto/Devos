# TASK-001 — Die Provenienz wird geprüft, nicht geglaubt

**Schiene:** Pilot · **Geschätzt:** 1 – 2 h · **Branch:** `codex/devos-gate-evidence`

**Stand:** Korrektur implementiert, unabhängiges Review und menschliche Abnahme ausstehend.
Basis: `c7727bb5e9c42314fc844a7abb6b1bb49c63891d`. Builder der Korrektur: Codex/OpenAI.

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
- `python3 tools/selftest.py` endet mit Exit 0, und keine der 176 am Basiscommit vorhandenen Proben wurde
  abgeschwächt, um das zu erreichen
- volle SHA256-Hashes binden die Provenienz an die tatsächlich vorgelegten Paket-, Kontext- und
  Ergebnisdateien; ein abweichendes Artefakt zählt nicht als gemessener Nachweis
- jeder geforderte Acceptance-Punkt wird nach Formatnormalisierung vollständig und genau einmal
  bewertet; gemeinsame Satzanfänge oder doppelte Einträge reichen nicht (G07)

## Forbidden

- keine neue Abhängigkeit außerhalb der Standardbibliothek
- keine Verschiebung der Programmgrenzen: `review_dispatch.py` rechnet weiterhin kein Gate, und
  `review_result.py` versendet weiterhin nichts
- keine vorhandene Probe entschärfen, umbenennen oder entfernen, damit eine neue besteht
- kein neues Konfigurationsschalterwerk; die Prüfung ist an oder das Gate ist keins

## Relevante Beschlüsse

- G01 — die Begründung dafür, dass eine parsende JSON-Datei kein Beleg ist
- G04 — warum die Familientrennung überhaupt nachgewiesen werden muss

## Ergänzter Umfang und Grenzen

Mit Zustimmung zur Empfehlung wurde die Paketbindung in den Korrekturumfang aufgenommen,
einschließlich Kontext und Ergebnis, sowie die separat reproduzierte Acceptance-Lücke G07.
Das Gate verlangt Provenienzschema 1.2 und volle Hashes. Ältere Versionen werden mit Grund
verworfen; eine vorhandene Reviewer-Selbstauskunft wird weiterhin ausdrücklich als solche geführt.
Ohne verwertbare Provenienz und ohne Selbstauskunft bleibt PASS unerreichbar.

Die Bindung prüft lokale Konsistenz. Sie beweist weder die Identität eines entfernten Anbieters
noch schützt sie vor gemeinsam gefälschten Dateien. Die Abnahme bleibt offen; ein grüner
Eigentest ersetzt das unabhängige Review nicht. Die anfängliche Task nannte 121 Bestandsproben;
am Basiscommit sind es 176, die mit dieser Korrektur erhalten bleiben.
