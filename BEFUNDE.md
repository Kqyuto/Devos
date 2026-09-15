# Befunde G01–G05 — reproduziert, korrigiert, belegt

Ein Reviewer hat bei einer Codelektüre vier mögliche Lücken benannt. Eine Lektüre ist keine
Reproduktion: ein benannter Defekt, den niemand vorführen kann, ist eine Vermutung, und eine
Korrektur ohne vorgeführten Fall trifft womöglich etwas anderes als das Gemeldete.

Deshalb hat jeder Befund hier drei Teile: **wie er vorgeführt wird**, **was daraufhin passiert ist**
und **woran man sieht, dass er nicht zurückkommt.**

```bash
python3 tools/reproduce_findings.py   # Exit 1, solange ein Fall vorführbar ist
python3 tools/selftest.py             # 97 Proben, Exit 1 bei Fehlschlag
```

Am Commit `e17b2b9` (Stand vor dieser Arbeit) waren **13 von 14 Fällen vorführbar**. Der vierzehnte
ist widerlegt und unten als solcher geführt.

---

## G01 — `review_context.json` wurde nicht strukturell geprüft

**Gemeldet:** der Kontext wird nicht gegen ein Schema validiert.
**Status: bestätigt, in zwei Ausprägungen, davon eine schlimmer als gemeldet.**

Das Gate las den Kontext mit `json.loads` und arbeitete mit allem weiter, was parste.

| Vorgeführt | Verhalten vorher |
|---|---|
| Kontext ist `[]` statt eines Objekts | `AttributeError` — **Absturz mit Exit 1**, und Exit 1 heißt beim Aufrufer `CHANGES_REQUIRED` |
| `"tests": {"passed": "false"}` — die Zeichenkette `"false"` ist in Python wahr | **Exit 0, `MERGEABLE_PENDING_HUMAN`** |
| `"task"` ist ein String statt eines Objekts | `AttributeError` — Absturz |

Der Absturz ist der teurere Teil und stand nicht in der Meldung: ein Programmfehler erschien als
Gate-Urteil. Wer `run_review.py` benutzt, hätte „CHANGES_REQUIRED" gelesen, wo in Wahrheit nichts
geprüft wurde.

**Korrektur.** `schema/review_context.schema.json` beschreibt den Kontext vollständig, mit
`additionalProperties: false` und echten Typen. `review_result.py:load_context()` prüft die Datei,
bevor irgendeine Regel sie anfasst; ein Strukturfehler ist ein benannter Befund und macht `PASS`
unerreichbar. Zusätzlich prüft das Gate Wahrheitswerte mit `is True` statt auf Wahrheitsähnlichkeit —
`"false"` kommt jetzt an zwei Stellen nicht mehr durch.

**Regressionsproben:** `G01` × 4 im Eigentest.

---

## G02 — die Acceptance-Abdeckung wurde nicht abgeglichen

**Gemeldet:** die bewerteten Punkte werden nicht gegen die geforderten geprüft.
**Status: bestätigt.**

Das Gate las `reviewed.acceptance_items` und suchte darin nach `not_met` und `unverifiable`. Eine
**leere Liste** enthält beides nicht.

| Vorgeführt | Verhalten vorher |
|---|---|
| Task fordert drei Punkte, Reviewer bewertet **keinen** | Exit 0, `PASS` |
| Task fordert drei, Reviewer bewertet einen | Exit 0, `PASS` |
| Reviewer bewertet einen Punkt, den die Task nicht kennt | Exit 0, `PASS` |

Das ist die Fehlerklasse des Werkzeugs in Reinform: das Gate prüfte die *Antworten*, ohne zu prüfen,
ob überhaupt jemand die *Fragen* gestellt hatte.

**Korrektur.** Das Gate gleicht jetzt beide Richtungen ab — nicht bewertete Forderungen
(`acceptance_uncovered`) und Bewertungen ohne Entsprechung (`acceptance_unbound`); beide erzwingen
`INSUFFICIENT_CONTEXT`. Der Abgleich normalisiert Groß-/Kleinschreibung, Satzzeichen und
Markdown-Auszeichnung, damit nicht die Formatierung entscheidet, und akzeptiert Enthaltensein ab
zwölf Zeichen. Eine Task **ohne** Acceptance-Punkte ist selbst ein Befund: dann gibt es nichts,
wogegen geprüft werden könnte. Die Reviewer-Anweisung verlangt den Wortlaut ausdrücklich.

*Bekannte Grenze, ausdrücklich benannt:* der Abgleich ist Textvergleich. Ein Reviewer, der einen
Punkt vollständig umformuliert, erzeugt `INSUFFICIENT_CONTEXT` statt eines stillen `PASS` — das ist
die richtige Richtung für einen Irrtum, aber es ist ein Irrtum, und er kostet eine Runde.

**Regressionsproben:** `G02` × 5.

---

## G03 — der Testnachweis wurde geglaubt, nicht nachgerechnet

**Gemeldet:** Auswertung über `ran`/`passed`/`isolated`; ein eigener Abgleich von Testrevision und
Exit-Code fehlt.
**Status: bestätigt.**

`passed` und `ran_against` schreibt der *Erzeuger* des Pakets. Das Gate übernahm sie ungeprüft,
obwohl `exit_code` und `range.head` danebenstanden.

| Vorgeführt | Verhalten vorher |
|---|---|
| `exit_code: 1`, `passed: true` | Exit 0, `PASS` |
| Tests liefen gegen eine **fremde Revision** | Exit 0, `PASS` |
| Testlauf ohne `exit_code` und ohne Revisionsangabe | Exit 0, `PASS` |

**Korrektur.** Der Testnachweis wird gegengerechnet: `exit_code` gegen `passed` (jeder Widerspruch
ist `INSUFFICIENT_CONTEXT` — welches Feld lügt, ist von außen nicht entscheidbar), `exit_code != 0`
ohne Widerspruch ist `CHANGES_REQUIRED`, `ran_against` muss zeichengenau der geprüfte Head sein, und
ein Lauf ohne Exit-Code oder ohne Revisionsangabe ist kein Nachweis.

**Regressionsproben:** `G03` × 5, plus die E2E-Probe, die bindet, was der Erzeuger wirklich schreibt.

---

## G04 — die Modellfamilien-Trennung wurde nirgends überprüft

**Gemeldet:** die Trennung wird der Konfiguration überlassen.
**Status: bestätigt — und das ist der schwerste der fünf.**

Die getrennte Modellfamilie ist die einzige Gegenmaßnahme des Werkzeugs gegen seine eigene
Fehlerklasse: *ein Prüfer, der die Interpretationslogik des Erzeugers teilt, bestätigt dessen
Irrtümer.* Sie stand in einer Umgebungsvariablen und wurde von keiner Zeile nachgerechnet.

| Vorgeführt | Verhalten vorher |
|---|---|
| `DEVOS_REVIEWER_MODEL=claude-opus-5` bei Builder `claude-opus-5` | Transport **sendet**, Exit 0 |
| dasselbe Modell auf beiden Seiten, Gate | Exit 0, `PASS` |
| gar kein Nachweis der Trennung | Exit 0, `PASS` |

Die Provenienz hielt das Modell fest — aber niemand las sie. Ein Modell durfte sich selbst prüfen
und erzeugte ein grünes Gate.

**Korrektur.** `tools/model_family.py` bestimmt Familien aus Modellnamen (mit Anbieterpräfixen wie
`us.anthropic.…`) und erlaubt eine ausdrückliche Erklärung für unbekannte Namen. Die Aufteilung der
Durchsetzung folgt der Beweislage:

- **Der Transport verweigert den Versand**, wenn die Trennung *nachweislich verletzt* ist —
  bevor Aufwand entsteht.
- **Das Gate verweigert `PASS`**, wenn sie *nicht nachgewiesen* ist. Der neue Status
  `INDEPENDENCE_UNPROVEN` kann von keinem Reviewer erklärt werden; nur die Maschine setzt ihn.

Die dritte Regel ist die, an der man es falsch machen kann: **unbekannt ist nicht verschieden.**
Zwei Modellnamen, die keine Tabelle kennt, können dasselbe Modell hinter zwei Aliassen sein. Sie
gelten als *nicht nachgewiesen*, nie als getrennt. `separated` ist deshalb dreiwertig.

Eine Selbstauskunft des Reviewers (`reviewer.model` im Ergebnis) zählt, wenn keine Transport-
Provenienz vorliegt — sie steht dann aber als *ungemessen* im Ergebnis und auf dem
Entscheidungsblatt.

**Regressionsproben:** `G04` × 6, plus zwei E2E-Proben (Versand verweigert / Trennung nachgewiesen).

---

## G05 — der Schemaprüfer behauptete mehr, als er durchsetzt *(eigener Befund)*

Bei der Arbeit an G01 gefunden, nicht gemeldet: `jsonschema_mini.SUPPORTED` führte `allOf` und
`anyOf`, aber `validate()` sah sie nie an. Die Datei, deren erklärter Zweck es ist, ihre eigene
Grenze zu benennen, benannte sie falsch — `unsupported_keywords()` hätte zu einem Schema mit `anyOf`
geschwiegen, und die Regel wäre stillschweigend ungeprüft geblieben.

**Korrektur.** Beide aus `SUPPORTED` entfernt; `type` darf jetzt eine Liste sein
(`["integer","null"]`), was das Kontextschema wirklich braucht. Eine Probe hält fest, dass die
eigenen Schemata nur durchgesetzte Konstrukte benutzen.

**Widerlegt (G05.2):** die Vermutung, in den vorhandenen Schemata stünden bereits ungeprüfte
Konstrukte, ließ sich **nicht** vorführen — `review_result.schema.json` war und ist sauber. Der Fall
bleibt als Probe stehen, weil er den *nächsten* solchen Fehler fängt, nicht weil er einen gefunden
hätte.

---

## Was diese Korrekturen nicht sind

Ein grüner Eigentest ist kein unabhängiges Review. Alles oben ist vom **Builder** geschrieben und
vom Builder geprüft — dieselbe Interpretationslogik, gegen die das ganze Werkzeug gebaut ist. Die
Proben zeigen, dass der *vorgeführte* Fall nicht mehr durchgeht. Sie zeigen nicht, dass es keinen
benachbarten Fall gibt, an den niemand gedacht hat.

Der erste echte Pilottask dieses Werkzeugs ist deshalb: **diese Korrekturen von einem Reviewer der
anderen Familie prüfen lassen** (`work/tasks/TASK-001-devos-gate-korrektur.md`). Bis das geschehen
ist, gilt für G01–G05 dasselbe wie für jede andere Lieferung — belegt, aber nicht unabhängig geprüft.
