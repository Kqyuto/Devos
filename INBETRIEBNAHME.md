# Inbetriebnahme — was läuft, was simuliert ist, was fehlt

Dieses Dokument trennt drei Dinge, die sonst ineinanderlaufen: **geprüft an echten Daten**,
**geprüft gegen eine Attrappe** und **überhaupt nicht geprüft**. Die zweite Kategorie ist die
gefährliche — sie sieht in einem grünen Testlauf genauso aus wie die erste.

Stand: Eigentest `131 von 131`, `tools/reproduce_findings.py` Exit 0.

---

## 1 · Was tatsächlich läuft

Alles hier wurde gegen echte git-Repositories, echte Dateien und den echten Code ausgeführt —
nicht gegen eine Beschreibung davon.

| Was | Beleg |
|---|---|
| Paketerzeugung aus einer echten Lieferung: Diff, Umbenennungen, Binärdateien, Größenlimits, Weglass-Liste | `F03/F04`-Proben, `E2E` |
| Maschinentests im **isolierten Worktree** des geprüften Heads | `F03`-Proben; eine nur lokal vorhandene Datei kann den Test nachweislich nicht grün färben |
| Normquellen im Wortlaut aus vier verschiedenen Registerformen | an den **echten** mahoraga-Registern geprüft: `D-001`, `OQ-001`, `R-001`, `DEP-01` kommen vollständig an, 19 von 19 IDs aufgelöst |
| Das Gate mit allen Regeln aus `BEFUNDE.md` | `F01`–`F05`, `N01`–`N02`, `G01`–`G05`, 121 Proben |
| Der Orchestrator: Runden, Budget, Zustand, Fortsetzung nach Absturz | `O1`–`O10` gegen echte Repos mit echtem Builder-Skript |
| Rote Tests rufen den Reviewer nicht | `O1` |
| Zwei Korrekturrunden als harte Grenze | `O2`, `O3` |
| Fortsetzung wiederholt den Builder-Aufruf nicht | `O5` — gezählt über eine Aufruf-Logdatei |
| Geänderte Taskdatei bricht die Fortsetzung ab | `O6` |
| Builder ohne Commit / mit schmutzigem Verzeichnis → Blockade | `O7`, `O8` |
| Das Entscheidungsblatt aus vorhandenen Daten | `O1`; es nennt den geprüften Commit und meldet, wenn der Arbeitsstand woanders steht |
| Die Quellen-Nachforderung liest aus der **Revision**, nicht aus dem Arbeitsverzeichnis | `E2E` — eine lokal manipulierte Datei erreicht den Reviewer nachweislich nicht |
| Der Schlüssel taucht in keiner geschriebenen Datei und in keiner Ausgabe auf | `N01` |
| Ein fremdes Projekt kann sein Paket nicht ins DevOS-Repo schreiben | `S` |

## 2 · Was nur gegen eine Attrappe geprüft ist

**In dieser Session wurde kein einziges Mal ein echtes Modell aufgerufen.** Das ist die wichtigste
Zeile dieses Dokuments.

| Was | Wodurch ersetzt | Was der grüne Test deshalb **nicht** zeigt |
|---|---|---|
| Der Reviewer | Ein lokaler HTTP-Server auf `127.0.0.1`, der vorgeschriebenes JSON zurückgibt | Ob ein echtes Modell schemagültig antwortet, brauchbare Befunde findet, oder überhaupt etwas findet. Geprüft ist der **Transport**, nicht das **Urteil** |
| Der Builder | Ein Bash-Skript mit vier Zeilen | Ob ein Modell als Builder den Vertrag einhält: committen, sauber hinterlassen, den Brief lesen, den Umfang nicht ausweiten |
| Token und Kosten | Vom Mock erfundene Zahlen (`prompt_tokens: 900`) | Was ein Lauf wirklich kostet. Die Rechenkette ist geprüft, die Größenordnung ist **unbekannt** |
| Die Quellen-Nachforderung | Ein Mock, der genau die geplanten Pfade nennt | Ob ein echtes Modell `missing_context` mit Pfaden füllt statt mit Prosa wie „mehr Kontext zur Architektur" |

### Das größte Risiko des ersten echten Laufs

Der Abgleich der Acceptance-Abdeckung (`G02`) vergleicht **Text**. Die Reviewer-Anweisung verlangt
den Wortlaut, und das Schema erzwingt ihn nicht — das kann es nicht. Formuliert ein echter Reviewer
einen Punkt um, statt ihn zu kopieren, meldet das Gate „nicht bewertet" und erzeugt
`INSUFFICIENT_CONTEXT`.

Das ist die **richtige Richtung** für einen Irrtum — lieber ein Fehlalarm als ein stilles `PASS` —
aber es ist ein Irrtum, er kostet eine Runde, und **er ist nie gegen ein echtes Modell geprüft
worden.** Wenn beim ersten Pilotlauf etwas schiefgeht, ist das der wahrscheinlichste Ort.

*Gegenmaßnahme, falls es eintritt:* nicht die Regel aufweichen, sondern die Anweisung schärfen und
den Fall als Task schneiden. Eine Abdeckungsprüfung, die man wegkonfigurieren kann, ist keine.

## 3 · Was für einen echten Durchlauf fehlt

| # | Fehlt | Warum es blockiert | Woran man erkennt, dass es da ist |
|---|---|---|---|
| 1 | **Ein Reviewer-Zugang** — `DEVOS_REVIEWER_API_KEY` und ein erreichbarer Endpunkt | Ohne ihn gibt es kein zweites Urteil, und das ganze Verfahren ist eine Selbstbestätigung | `review_dispatch.py` endet mit Exit 0 statt 2 |
| 2 | **Ein ausführender Rechner** | Die Umgebung, in der dies gebaut wurde, ist flüchtig und hat **keinen Netzzugang zu Modellanbietern**: `api.openai.com` antwortet hier `403 Forbidden` am Proxy. Ein Runner muss dort stehen, wo Schlüssel und Netz sind | Ein `run_task.py`-Lauf, der Exit 0 oder 1 liefert statt Exit 5 |
| 3 | **Ein Builder-Zugang auf dem Runner** — `DEVOS_BUILDER_CMD`, das ein Modell nicht-interaktiv startet und Schreibrecht im Repo hat | Ohne ihn hält der Lauf nach dem Brief an; das Verfahren läuft, aber nicht allein | `O1`-Verhalten außerhalb des Eigentests |
| 4 | **Ein Klon von mahoraga auf dem Runner mit Push-Recht** | mahoraga ist privat. Die GitHub-Verbindung allein startet keine Modellläufe | `run_task.py --root <mahoraga>` erzeugt ein Paket |
| 5 | **Token-Preise** — `DEVOS_REVIEWER_PRICE_IN/OUT` | Ohne sie werden Token gezählt, aber das Kostenlimit **bindet nicht**. Das Werkzeug sagt das bei jedem Lauf | `spent.cost_usd` ist nicht `null` |

**Was ausdrücklich nicht fehlt:** Zugangsdaten gehören in die Laufzeitumgebung des Runners, nicht in
eines der beiden Repositories. Beide `.gitignore` nehmen Laufmaterial aus, und `review_request.py`
verweigert, ein Paket eines fremden Projekts ins DevOS-Repo zu schreiben — mahoraga-Inhalt kann so
nicht versehentlich im öffentlichen Werkzeug-Repo landen.

## 4 · Der Pilotplan

**Pilot 1 — eine kleine DevOS-Gate-Korrektur.**
`work/tasks/TASK-001-devos-gate-korrektur.md`, Befund `G06`: das Gate prüft die Provenienz nicht,
aus der es den Nachweis der Familientrennung liest. Klein, von außen prüfbar, und er betrifft genau
das, worauf das Verfahren beruht. Er ist **absichtlich nicht behoben** — ihn vom selben Builder
beheben zu lassen, der ihn gefunden hat, wäre wieder eine Selbstbestätigung.

**Pilot 2 — ein eng begrenzter mahoraga-Task.**
`work/tasks/TASK-001-registerpruefer.md` im mahoraga-Repo: ein Registerprüfer mit Exit-Code. Er ist
nicht beliebig gewählt — mahoraga enthält **keine ausführbare Zeile**, und ohne Testkommando ist
`tests.ran = false`, womit das Gate jede Lieferung dieses Repos auf `CHANGES_REQUIRED` setzt,
unabhängig vom Inhalt. Vorher kann das Verfahren an mahoraga nicht arbeiten.

### Was über 3 – 5 echte Tasks gemessen wird

`tools/delivery_metrics.py --from-run work/runs` erzeugt die maschinell messbaren Ereignisse aus den
Laufzuständen. Von Hand bleiben **zwei Sorten Zeilen** — und beide bewusst:

| Größe | Woher |
|---|---|
| Durchlaufzeit, Korrekturrunden, Blocker, Modellnutzung in Token, Kosten | erzeugt aus `run_state.json` |
| **Menschenzeit** (`human_minutes`) | nur der Mensch weiß sie. Wird sie nicht gebucht, meldet das Werkzeug einen Befund und rechnet **nicht** mit null — sonst läse sich die ungemessene Lieferung als die beste der Reihe |
| **Abnahme** (`accepted`) | erst beim Merge. `PASS` ist keine Abnahme, und `--from-run` schreibt diese Zeile deshalb nie |
| **Fehlalarme** (`false_alarms` je Runde) | ein Urteil über ein Urteil; keine Maschine kann es fällen |
| **Nachträglich entdeckte Fehler** (`escaped_defect`) | die Zahl, die entscheidet, ob das Gate etwas taugt. Ein schnelles Gate, das Fehler durchlässt, ist teurer als keins |

Nach drei vollständig gemessenen Lieferungen rechnet `delivery_metrics.py` Menschenanteil und
Durchsatz gegen 6 – 14 h/Woche. Bei weniger als drei sagt es das und urteilt nicht.

## 5 · Graphify — bewusst nicht angefasst

Nichts in diesem Stand hängt an Graphify, und nichts ist darauf vorbereitet worden. Das ist
Absicht: eine Kontextsuche ist eine Verbesserung eines Ablaufs, der erst einmal laufen muss.

Vor einer Integration zu klären, in dieser Reihenfolge:

1. **Welches Graphify ist gemeint** und was es tatsächlich kann — das ist bisher nicht verifiziert.
2. Ob jeder Treffer **Revision und Originalstelle** nennt. Ein Treffer ohne Revision ist im
   Verfahren wertlos: das Gate bindet alles an SHAs.
3. Ob ein **veralteter oder unvollständiger Index erkennbar** ist. Ein Index, der still veraltet,
   ist genau die zweite, nicht auditierbare Wahrheit, die das Projekt sonst überall vermeidet.
4. Vergleichbare Tasks **mit und ohne** Graphabruf, gemessen mit derselben Metrik wie oben —
   Indexaufbau und -pflege in den Kosten enthalten.

Zwei Regeln gelten unabhängig vom Ergebnis:

- **Der Graph darf den Prüfumfang nicht begrenzen.** Die Bestandsliste im Paket und die
  Quellen-Nachforderung sind genau dafür da, dass der Reviewer über jede Vorauswahl hinausgreifen
  kann — auch über die eines Graphen.
- **Eine Builder-Beziehung wie `satisfies → AC-1` ist eine Behauptung, kein Nachweis.** Sie gehört,
  wenn sie ungeprüft mitgeliefert wird, nach `unproven_claims` — dorthin, wo das Schema
  Behauptungen ohne Beleg schon heute hinstellt.
