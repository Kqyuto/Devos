# DevOS — Zwei-Modell-Review als Werkzeug

> **Du willst nur loslegen?** [`START.md`](START.md) — Schlüssel eintragen,
> `devos preflight --smoke`, mahoraga anschließen. Drei Schritte.

Ein Builder-Modell schreibt, ein Reviewer-Modell **einer anderen Familie** prüft, eine
deterministische Maschine rechnet das Gate, **der Mensch entscheidet**. Dazwischen trägt
niemand Dateien.

Das Werkzeug ist projektunabhängig. Es kennt kein Register, keinen Pfad und keine
ID-Form — das steht in der `.devos.json` des geprüften Projekts. Python 3.9+,
Standardbibliothek, **keine Installation**. Ein Gate, das von einem Installationsschritt
abhängt, wird in der Praxis optional, und ein optionales Gate ist keins.

---

## Die Schleife

```
  MENSCH ── schneidet die Task ──►  <projekt>/work/tasks/TASK-nnn.md
                                             │
  run_task.py ── fährt den ganzen Lauf und hält den Zustand fest ────────────┐
                                             │                               │
  BUILDER ── implementiert + Tests ── Commit auf einen Branch                │
      Auftrag aus BUILD-BRIEF.md · erzeugt, nicht gepflegt                   │
                                             │                               │
  MASCHINENTESTS ── im isolierten Worktree des geprüften Heads               │
      rot ⇒ der Reviewer wird gar nicht erst gerufen ───────────────────────►│
                                             │                       Korrektur
  review_request.py ──►  REVIEW-REQUEST.md + review_context.json            │
      vollständiger Diff · Testnachweis an die Revision gebunden             │
      Normquellen im Wortlaut · Bestandsliste · Weglass-Liste                │
                                             │                               │
  review_dispatch.py ──►  übergibt und holt zurück                           │
      Provenienz: Modell · Familie · Token · Kosten · Versuche               │
      EINE Quellen-Nachforderung aus der geprüften Revision                  │
                                             │                               │
  REVIEWER (andere Familie) ──►  review_result.json                          │
      PASS · CHANGES_REQUIRED · BLOCKED · CONFLICT · INSUFFICIENT_CONTEXT    │
                                             │                               │
  review_result.py ──►  Gate + Exit-Code                                     │
      0 MERGEABLE_PENDING_HUMAN · 1 CHANGES_REQUIRED ──────────────────────►─┘
      2 HUMAN_DECISION_REQUIRED · 3 INVALID_RESULT        höchstens 2 Runden
                                             │
  handover.py ──►  HANDOVER.md — das Entscheidungsblatt, erzeugt
                                             │
  MENSCH ── entscheidet. Der Merge bleibt Handarbeit. ─────┘
```

---

## Einrichten

```bash
git clone <dieses-repo> ~/devos
export PATH="$HOME/devos/bin:$PATH"

devos selftest                           # 182 Proben, Exit 1 bei Fehlschlag

mkdir -p ~/.config/devos
cp ~/devos/templates/env.example ~/.config/devos/env
chmod 600 ~/.config/devos/env
$EDITOR ~/.config/devos/env              # eine Zeile: DEVOS_REVIEWER_API_KEY

devos preflight --smoke                  # sagt, was noch fehlt — und probt den echten Lauf
```

Die Zugangsdatei liegt **außerhalb beider Repositories**. Ein Schlüssel im
Arbeitsverzeichnis wird irgendwann mitcommittet — nicht aus Nachlässigkeit, sondern weil
`git add -A` genau dafür gebaut ist. Die Umgebung gewinnt immer gegen die Datei, damit ein
einzelner Lauf gezielt anders konfiguriert werden kann.

Optional, aber nützlich:

| Variable | Wofür |
|---|---|
| `DEVOS_BUILDER_CMD` | Kommando, das den Builder startet. Fehlt es, liefert der Mensch den Commit |
| `DEVOS_REVIEWER_FAMILY` · `DEVOS_BUILDER_FAMILY` | Familie ausdrücklich erklären, wenn der Modellname in keiner Tabelle steht |
| `DEVOS_REVIEWER_PRICE_IN` · `_OUT` | USD je 1 Mio. Token. **Ohne sie werden Token gezählt, aber keine Kosten berechnet** — und das Kostenlimit bindet nicht |
| `DEVOS_REVIEWER_TIMEOUT` | Sekunden, Standard 600 |

Im geprüften Projekt eine `.devos.json` anlegen (Vorlage:
`templates/devos.json.example`):

```json
{
  "project": "meinprojekt",
  "id_pattern": "\\b(D-\\d{3}|OQ-\\d{3})\\b",
  "registers": {
    "D":  {"path": "registers/decisions.md", "kind": "heading",
           "heading_pattern": "^#{1,4}\\s+D-\\d{3}\\b"},
    "OQ": {"path": "registers/open-questions.md", "kind": "row"}
  },
  "paths": {"tasks": "work/tasks", "review": "work/review", "runs": "work/runs",
            "deliveries": "work/deliveries.jsonl"},
  "limits": {"max_correction_rounds": 2, "max_wall_minutes": 180,
             "max_builder_calls": 3, "max_reviewer_calls": 3, "max_cost_usd": null},
  "builder": {"cmd": "…", "timeout_seconds": 3600}
}
```

`kind` ist `heading` (Abschnitt bis zur nächsten gleichrangigen Überschrift) oder `row`
(Tabellenzeilen, die mit `| <ID> ` beginnen). Weitere Felder, jedes aus einem Fehlalarm an
einem echten Bestand entstanden:

| Feld | Wofür |
|---|---|
| `"tests": "…"` | Das Testkommando des Projekts. Steht es hier, tippt es niemand zweimal, und Bereitschaftstest wie Orchestrator benutzen dieselbe Zeile |
| `"multi_row": true` je Register | Das Register führt **mehrere Zeilen je ID** und die letzte gilt. Ohne diese Angabe meldet der Prüfer jede Fortschreibung als Doppeldefinition |
| `"ignore_paths": ["tools/*.py"]` | Nicht nach Referenzen durchsuchen. Eine ID in einem Testfixture ist keine Referenz |
| `"external_ids": {"X-1": "Herkunft"}` | IDs, die dem Muster entsprechen, aber einem anderen Projekt gehören |

**Und die wichtigste Grenze zuerst:** `check_registers.py` ist für Projekte gedacht, die noch
keine eigenen Rechner haben. Ein Projekt mit eigenen Gates benutzt **die** als `--tests` — sie
kennen seine Regeln, der generische Prüfer kennt nur `.devos.json`. Der Register-Präfix ist der Buchstabenteil
der ID: `D-144` → `D`, `OQ-003` → `OQ`, `G01` → `G`. **Fehlt die Datei, läuft das Werkzeug
weiter — aber ohne Normquellen, und es sagt das in `omitted`**, statt so zu tun, als
hätte es nichts zu holen gegeben.

---

## Benutzen

**Ein Lauf, vom Bau bis zum Entscheidungsblatt:**

```bash
cd ~/meinprojekt

python3 ~/devos/tools/run_task.py \
    --task work/tasks/TASK-001-....md \
    --onto main \
    --tests "python3 -m unittest discover"
echo $?        # 0 = der Mensch darf jetzt entscheiden und übernehmen

cat work/runs/TASK-001/HANDOVER.md
```

Ohne konfigurierten Builder (`--no-builder`) schreibt der Lauf den Auftrag nach
`work/runs/<task>/round-N/BUILD-BRIEF.md` und hält an. Sobald der Commit da ist, dasselbe
Kommando erneut: der Lauf wird **fortgesetzt**, nicht wiederholt.

**Nur ein einzelnes Review, ohne Laufzustand:**

```bash
python3 ~/devos/tools/run_review.py --task work/tasks/TASK-001-....md \
    --onto main --tests "python3 -m unittest discover" --out work/review
python3 ~/devos/tools/delivery_metrics.py --log work/deliveries.jsonl
```

`--dry-run` erzeugt Paket und Anfrage, ohne zu senden. Die drei Schritte lassen sich
auch einzeln fahren (`review_request.py` · `review_dispatch.py` · `review_result.py`) —
sie bleiben **getrennte Programme**: der Versender darf kein Gate-Ergebnis erzeugen
können, und das Gate darf nichts versenden.

---

## Was der Lauf mechanisch durchsetzt

| Regel | Warum |
|---|---|
| **Höchstens zwei Korrekturrunden insgesamt** | Eine Korrektur nach rotem Test zählt wie eine nach einem Reviewer-Befund. Danach entscheidet der Mensch, auch über einen unfertigen Stand |
| Rote Maschinentests rufen den Reviewer **nicht** | Ein fehlgeschlagener Test ist eine Reparatur, kein Reviewgegenstand — und ein gesparter Aufruf |
| `CONFLICT`, fehlender Kontext, technische Blockade, erschöpftes Budget → **Human Gate** | Alle vier enden am selben Ort. Kein Sonderweg |
| Zeit, Builder-Aufrufe, Reviewer-Aufrufe, Kosten sind **endlich vorgegeben** | Kein Wert ist unbegrenzt. Das Kostenlimit bindet nur mit konfigurierten Token-Preisen — und sagt das, statt eine Zahl zu erfinden |
| Der Laufzustand wird nach **jedem** Schritt atomar geschrieben | Ein Zustand, den man erst am Ende schreibt, fehlt nach einem Absturz genau dann, wenn er gebraucht wird. Der teure Builder-Aufruf wird nicht wiederholt |
| Bewegt sich der Head, **verfallen** Paket und Urteil der Runde | Jeder neue Commit braucht neue Tests und ein neues Review |
| Ändert sich die Taskdatei, wird die Fortsetzung **verweigert** | Ein Urteil an einem Gegenstand, den es nicht mehr gibt, ist schlimmer als kein Urteil |
| Eine Lieferung, die **kein Commit** ist, wird nicht geprüft | Bleibt nach dem Bau etwas im Arbeitsverzeichnis liegen, hängt das Urteil an einem Stand, den keine Revision benennt |
| **`PASS` heißt: bereit für die menschliche Entscheidung** | Kein Programm dieser Kette mergt, checkt aus oder verschiebt einen Branch |

## Was das Gate mechanisch durchsetzt

Statusvorrang: `INSUFFICIENT_CONTEXT > CONFLICT > INDEPENDENCE_UNPROVEN > BLOCKED >
CHANGES_REQUIRED > PASS`. `INDEPENDENCE_UNPROVEN` kann kein Reviewer erklären; nur die
Maschine setzt ihn.

| Regel | Warum |
|---|---|
| Ohne verwertbaren Kontext ist `PASS` unerreichbar | Ein fehlender `--context` war anfangs Exit 0 — stiller Freibrief |
| **Der Kontext wird gegen ein Schema geprüft, bevor eine Regel ihn anfasst** | Eine JSON-Liste ließ das Gate abstürzen — Exit 1, was der Aufrufer als `CHANGES_REQUIRED` liest. Und `"passed": "false"` ist in Python wahr (G01) |
| `task_id`, `base`, `head` werden als **volle SHAs exakt** verglichen | Präfixvergleiche binden ein Urteil nicht an eine Lieferung |
| Volle Schemaprüfung: Typen, Enums, verschachtelte Pflichtfelder, `additionalProperties` | `judged:"banana"` und `blocking:{}` kamen sonst durch |
| Acceptance ist Gate-Bedingung: `not_met` schließt `PASS` aus, `unverifiable` erzwingt `INSUFFICIENT_CONTEXT` | Ein grünes Gate über einem unerfüllten Kriterium ist ein stiller Defekt |
| **Jeder geforderte Acceptance-Punkt muss bewertet worden sein — und jede Bewertung muss zu einem gehören** | Eine leere `acceptance_items`-Liste enthält weder `not_met` noch `unverifiable`: ein Reviewer, der nichts bewertete, bekam `PASS` (G02) |
| `context_sufficient: false` erzwingt `INSUFFICIENT_CONTEXT` | Ein unvollständig informierter Reviewer liefert sonst ein selbstbewusstes `PASS` |
| Nicht leeres `blocking` schließt `PASS` aus | |
| `governance_conflicts` erzwingt `CONFLICT` — aus **jedem** Ausgangsstatus | |
| Fehlende, nicht gelaufene, fehlgeschlagene oder **nicht isolierte** Tests schließen `PASS` aus | Sonst färbt lokaler Reparaturcode einen älteren fehlerhaften Commit grün |
| **Der Testnachweis wird nachgerechnet**: `exit_code` gegen `passed`, `ran_against` gegen den geprüften Head | Beide Felder schreibt der *Erzeuger*. Ein Widerspruch ist `INSUFFICIENT_CONTEXT` — welches Feld lügt, ist von außen nicht entscheidbar (G03) |
| **Die Familientrennung muss nachgewiesen sein** | Sie ist die einzige Gegenmaßnahme gegen die Fehlerklasse, wegen der es dieses Werkzeug gibt — und wurde von keiner Zeile nachgerechnet (G04) |
| Nicht schemagültige Reviewer-Antwort: **ein** Reparaturversuch, dann Abbruch ohne Datei | Kein Schleifendrehen, bis etwas parst |

Und im Erzeuger: **`omitted` ist Pflichtbestandteil jedes Pakets, auch leer.** Was nicht
mitging — zu groß, binär, unerwartet leerer Diff, gekürztes Protokoll, fehlende
Normquelle, unbestimmbare Builder-Familie — steht dort mit Grund. Ein Paket, das nicht
sagt, was es weggelassen hat, ist genau die Zusammenfassungsebene, auf der
Kontextverzerrung entsteht.

---

## Die Trennung der Modellfamilien

Die Durchsetzung folgt der Beweislage, und das ist kein Detail:

- **Der Transport verweigert den Versand**, wenn die Trennung *nachweislich verletzt* ist —
  bevor Aufwand entsteht.
- **Das Gate verweigert `PASS`**, wenn sie *nicht nachgewiesen* ist.

`separated` ist deshalb dreiwertig: `true`, `false`, `null`. **Unbekannt ist nicht
verschieden** — zwei Modellnamen, die keine Tabelle kennt, können dasselbe Modell hinter
zwei Aliassen sein. Für eigene Endpunkte und Aliasnamen:
`DEVOS_REVIEWER_FAMILY` / `DEVOS_BUILDER_FAMILY` oder `model_families` in der
`.devos.json`. Eine Erklärung ist eine Behauptung des Menschen, keine Messung — sie steht
als `declared_via` auf dem Entscheidungsblatt.

---

## Der Reviewer ist nicht auf die Auswahl des Builders beschränkt

Jedes Paket trägt den **Bestand des geprüften Stands** (alle Pfade, keine Inhalte). Braucht
der Reviewer eine Datei im Wortlaut, antwortet er `INSUFFICIENT_CONTEXT` und nennt die
Pfade in `missing_context`. Der Transport reicht sie **aus genau der geprüften Revision**
nach — `git show <head>:<pfad>`, nie aus dem Arbeitsverzeichnis, das Reparaturcode
enthalten kann, der zur Lieferung nicht gehört. Genau einmal, mit Größengrenzen, und jede
Nachforderung steht in der Provenienz und auf dem Entscheidungsblatt.

---

## Privates bleibt privat

Ein Review-Paket enthält den vollständigen Diff und die Normquellen des geprüften
Projekts. `review_request.py` **verweigert** deshalb, sein Ausgabeverzeichnis in das
DevOS-Repo zu legen, solange das geprüfte Projekt ein anderes ist. Pakete, Laufzustände
und Metriken gehören in das geprüfte Projekt und stehen dort in dessen `.gitignore`.

---

## Was läuft, was simuliert ist, was fehlt

In dieser Entwicklung wurde **kein einziges Mal ein echtes Modell aufgerufen.** Jede
Reviewer-Antwort in jeder Probe kam von einem lokalen HTTP-Server, jeder Builder war ein
Bash-Skript. Das prüft den Transport, das Gate, den Zustand und die Grenzen — es prüft
**nicht**, ob ein echtes Modell brauchbare Befunde liefert.

[`INBETRIEBNAHME.md`](INBETRIEBNAHME.md) trennt die drei Kategorien vollständig und nennt
die fünf Voraussetzungen, die für einen echten Durchlauf noch fehlen.

---

## Herkunft

Entstanden aus einem Assay des Projekts „Kapital 2026". Dessen teuerste Fehlerklasse
lautet dort **R-213**: *„Eine Governance-Maschine bestätigt einen falschen Zustand, wenn
Prüfer und Erzeuger dieselbe Interpretationslogik teilen."* Die getrennte Modellfamilie
ist die Antwort darauf — **eine Maßnahme, kein Nachweis.**

Der erste Lauf des Werkzeugs auf sich selbst fand fünf blockierende Defekte in einer
Lieferung, die der Builder für fertig gehalten hatte (`F01`–`F05`, `N01`–`N02`). Eine
spätere Lektüre durch ein Modell der anderen Familie fand fünf weitere (`G01`–`G05`) —
darunter, dass die Familientrennung selbst nie überprüft wurde. Alle sind in
[`BEFUNDE.md`](BEFUNDE.md) mit Reproduktion und Korrektur festgehalten;
`tools/reproduce_findings.py` führt sie am laufenden Werkzeug vor.

**`G06` ist offen** und ausdrücklich der erste Pilottask
([`work/tasks/TASK-001-devos-gate-korrektur.md`](work/tasks/TASK-001-devos-gate-korrektur.md)).
Ihn vom selben Builder beheben zu lassen, der ihn gefunden hat, wäre wieder eine
Selbstbestätigung — genau das, wogegen dieses Werkzeug gebaut ist.
