# TASK-nnn — Kurztitel

**Schiene:** S? · **Geschätzt:** ? – ? h · **Branch:** `claude/task-nnn-...`

## Gegenstand

Ein Absatz: was gebaut wird und was danach möglich ist, das vorher nicht möglich war.

## Acceptance

Jede Zeile muss von außen prüfbar sein. „Sauber implementiert" ist keine Acceptance.

- ein Test, der vorher fehlschlägt und nachher besteht, deckt X ab
- `python3 ... --selftest` endet mit Exit 0
- Y ist in Z dokumentiert

## Forbidden

Was diese Lieferung ausdrücklich **nicht** tut. Verhindert stilles Wachsen.

- keine Änderung an gelocktem Registermaterial
- keine neue Abhängigkeit außerhalb der Standardbibliothek

## Relevante Beschlüsse

- D-nnn — wofür
- OQ-nnn — offen, betrifft diese Lieferung wie

## Offen / Unentschieden

Was der Builder **nicht** selbst entscheiden darf und dem Menschen vorlegt.

- ...
