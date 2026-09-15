# DevOS — Zwei-Modell-Review als Werkzeug

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
  BUILDER ── implementiert + Tests ── Commit auf einen Branch
                                             │
  review_request.py ──►  REVIEW-REQUEST.md + review_context.json
      vollständiger Diff · Testlauf im isolierten Worktree des geprüften Heads
      Normquellen im Wortlaut · Weglass-Liste
                                             │
  review_dispatch.py ──►  übergibt und holt zurück
      Provenienz: Modell · Endpunkt · Prüfsummen · Versuche
                                             │
  REVIEWER (andere Familie) ──►  review_result.json
      PASS · CHANGES_REQUIRED · BLOCKED · CONFLICT · INSUFFICIENT_CONTEXT
                                             │
  review_result.py ──►  Gate + Exit-Code
      0 MERGEABLE_PENDING_HUMAN · 1 CHANGES_REQUIRED
      2 HUMAN_DECISION_REQUIRED · 3 INVALID_RESULT
                                             │
  MENSCH ── entscheidet. Der Merge bleibt Handarbeit. ─────┘
```

---

## Einrichten

```bash
git clone <dieses-repo> ~/devos
python3 ~/devos/tools/selftest.py        # 60 Proben, Exit 1 bei Fehlschlag

export DEVOS_REVIEWER_API_KEY=...        # Pflicht — gehört in die Umgebung, nie ins Repo
export DEVOS_REVIEWER_MODEL=gpt-5        # Pflicht — andere Familie als der Builder
export DEVOS_REVIEWER_BASE_URL=https://api.openai.com/v1   # Standard; jede
                                         # OpenAI-kompatible Schnittstelle geht
```

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
  "paths": {"tasks": "work/tasks", "review": "work/review",
            "deliveries": "work/deliveries.jsonl"}
}
```

`kind` ist `heading` (Abschnitt bis zur nächsten gleichrangigen Überschrift) oder `row`
(Tabellenzeilen, die mit `| <ID> ` beginnen). **Fehlt die Datei, läuft das Werkzeug
weiter — aber ohne Normquellen, und es sagt das in `omitted`**, statt so zu tun, als
hätte es nichts zu holen gegeben.

---

## Benutzen

```bash
cd ~/meinprojekt

python3 ~/devos/tools/run_review.py \
    --task work/tasks/TASK-001-....md \
    --onto main \
    --tests "python3 -m unittest discover" \
    --out work/review
echo $?        # 0 = der Mensch darf jetzt entscheiden und übernehmen

python3 ~/devos/tools/delivery_metrics.py --log work/deliveries.jsonl
```

`--dry-run` erzeugt Paket und Anfrage, ohne zu senden. Die drei Schritte lassen sich
auch einzeln fahren (`review_request.py` · `review_dispatch.py` · `review_result.py`) —
sie bleiben **getrennte Programme**: der Versender darf kein Gate-Ergebnis erzeugen
können, und das Gate darf nichts versenden.

---

## Was das Gate mechanisch durchsetzt

Statusvorrang: `INSUFFICIENT_CONTEXT > CONFLICT > BLOCKED > CHANGES_REQUIRED > PASS`.

| Regel | Warum |
|---|---|
| Ohne verwertbaren Kontext ist `PASS` unerreichbar | Ein fehlender `--context` war anfangs Exit 0 — stiller Freibrief |
| `task_id`, `base`, `head` werden als **volle SHAs exakt** verglichen | Präfixvergleiche binden ein Urteil nicht an eine Lieferung |
| Volle Schemaprüfung: Typen, Enums, verschachtelte Pflichtfelder, `additionalProperties` | `judged:"banana"` und `blocking:{}` kamen sonst durch |
| Acceptance ist Gate-Bedingung: `not_met` schließt `PASS` aus, `unverifiable` erzwingt `INSUFFICIENT_CONTEXT` | Ein grünes Gate über einem unerfüllten Kriterium ist ein stiller Defekt |
| `context_sufficient: false` erzwingt `INSUFFICIENT_CONTEXT` | Ein unvollständig informierter Reviewer liefert sonst ein selbstbewusstes `PASS` |
| Nicht leeres `blocking` schließt `PASS` aus | |
| `governance_conflicts` erzwingt `CONFLICT` — aus **jedem** Ausgangsstatus | |
| Fehlende, nicht gelaufene, fehlgeschlagene oder **nicht isolierte** Tests schließen `PASS` aus | Sonst färbt lokaler Reparaturcode einen älteren fehlerhaften Commit grün |
| Nicht schemagültige Reviewer-Antwort: **ein** Reparaturversuch, dann Abbruch ohne Datei | Kein Schleifendrehen, bis etwas parst |

Und im Erzeuger: **`omitted` ist Pflichtbestandteil jedes Pakets, auch leer.** Was nicht
mitging — zu groß, binär, unerwartet leerer Diff, gekürztes Protokoll, fehlende
Normquelle — steht dort mit Grund. Ein Paket, das nicht sagt, was es weggelassen hat,
ist genau die Zusammenfassungsebene, auf der Kontextverzerrung entsteht.

---

## Herkunft

Entstanden aus einem Assay des Projekts „Kapital 2026". Dessen teuerste Fehlerklasse
lautet dort **R-213**: *„Eine Governance-Maschine bestätigt einen falschen Zustand, wenn
Prüfer und Erzeuger dieselbe Interpretationslogik teilen."* Die getrennte Modellfamilie
ist die Antwort darauf — **eine Maßnahme, kein Nachweis.** Ob die Urteile tatsächlich
unabhängig zustande kamen, wird je Review gemessen: Kontextabdeckung, Divergenzrate,
verbleibende gemeinsame Fehlerursachen.

Der erste Lauf des Werkzeugs auf sich selbst fand fünf blockierende Defekte in einer
Lieferung, die der Builder für fertig gehalten hatte. Alle fünf wurden vor der Korrektur
gegen den Code reproduziert; keiner war ein Fehlalarm. Die Proben `F01`–`F05` und `N01`
im Eigentest halten sie fest.
