# Inbetriebnahme — was läuft, was simuliert ist, was fehlt

Dieses Dokument trennt drei Dinge, die sonst ineinanderlaufen: **geprüft an echten Daten**,
**geprüft gegen eine Attrappe** und **überhaupt nicht geprüft**. Die zweite Kategorie ist die
gefährliche — sie sieht in einem grünen Testlauf genauso aus wie die erste.

Stand: Eigentest `182 von 182`, `tools/reproduce_findings.py` Exit 0.

**Der kürzeste Weg von hier: [`START.md`](START.md).** Dieses Dokument ist die
Begründung dahinter — was belegt ist und was nicht.

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
| Der Bereitschaftstest benennt jeden fehlenden Posten samt Abhilfe | `P1`–`P10` |
| Der Kontextindex: revisionsgebunden, veraltet = unbenutzt, begrenzt nichts | `X1`–`X9`, gegen die **echten** mahoraga-Register: 290 IDs, 384 Kanten |
| Der Registerprüfer gegen die **echten** Register beider Repos | mahoraga grün nach einer echten Korrektur (`R-213` als auswärtige ID); fünf gemeldete „Doppeldefinitionen" waren **Fehlalarme** meines eigenen Prüfers und wurden vor dem Commit behoben |

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

**Gegenmaßnahme, gebaut:** der Transport fragt in diesem Fall **einmal** nach — mit den fehlenden
Punkten im Wortlaut und der ausdrücklichen Auflage, das Urteil *nicht* zu ändern, nur die Form.
Genau wie bei einem Schemafehler, und genauso begrenzt.

Drei Dinge daran sind wichtig:

- Der Transport benutzt **dieselbe Vergleichsfunktion** wie das Gate (`RR.acceptance_abdeckung`).
  Zwei Matcher für dieselbe Frage wären zwei Wahrheiten, und die zweite weicht irgendwann ab.
- Das Gate wird dadurch **nicht** milder. Bleibt der Reviewer unvollständig, wird sein Urteil
  durchgereicht und das Gate verweigert `PASS` wie zuvor. Der Transport repariert die Form, er
  entscheidet nichts.
- Die Reihenfolge ist: **erst** eine angeforderte Quelle nachreichen, **dann** nach der Abdeckung
  fragen. Umgekehrt würde man einen Reviewer, der gerade sagt „mir fehlt Kontext", auffordern,
  trotzdem alles zu beurteilen. Der erste Entwurf hatte genau diese Reihenfolge falsch; die
  E2E-Probe zur Quellen-Nachforderung hat es gefangen.

Proben: `E2E` × 4. Was bleibt: ob ein echtes Modell nach der Nachfrage tatsächlich zeichengenau
kopiert. Auch das ist bis Schritt 2 unbelegt.

## 3 · Was für einen echten Durchlauf fehlt

| # | Fehlt | Warum es blockiert | Woran man erkennt, dass es da ist |
|---|---|---|---|
| 1 | **Ein Reviewer-Zugang** — `DEVOS_REVIEWER_API_KEY` und ein erreichbarer Endpunkt. Gemeint ist ein **API-Schlüssel** (`sk-…`), nicht ein ChatGPT-Abo oder -Connector: DevOS ruft `POST /v1/chat/completions` selbst auf | Ohne ihn gibt es kein zweites Urteil, und das ganze Verfahren ist eine Selbstbestätigung | `review_dispatch.py` endet mit Exit 0 statt 2 |
| 2 | **Ein ausführender Rechner** | Die Umgebung, in der dies gebaut wurde, ist flüchtig und hat **keinen Netzzugang zu Modellanbietern**: `api.openai.com` antwortet hier `403 Forbidden` am Proxy. Ein Runner muss dort stehen, wo Schlüssel und Netz sind | Ein `run_task.py`-Lauf, der Exit 0 oder 1 liefert statt Exit 5 |
| 3 | **Ein Klon von mahoraga auf dem Runner mit Push-Recht** | mahoraga ist privat. Die GitHub-Verbindung allein startet keine Modellläufe | `devos preflight --root ~/mahoraga` ist grün |
| 4 | **Token-Preise** — `DEVOS_REVIEWER_PRICE_IN/OUT` | Ohne sie werden Token gezählt, aber das Kostenlimit **bindet nicht**. Das Werkzeug sagt das bei jedem Lauf | `spent.cost_usd` ist nicht `null` |

**Nicht mehr offen, seit dieser Runde:** der Builder-Zugang (`tools/builders/claude_code.sh`
ist gebaut und in `env.example` vorbelegt), das Testkommando für ein reines Dokumenten-Repo
(`tools/check_registers.py` arbeitet aus `.devos.json` heraus, ohne Domänenwissen), und die
Frage, wie man den Zustand überhaupt feststellt (`devos preflight`).

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

**Pilot 2 — mahoraga, und zwar zurückhaltender als geplant.**

Hier ist eine frühere Annahme dieses Dokuments **falsch gewesen**, und die Korrektur ist der
eigentliche Inhalt dieses Abschnitts: ich hatte gegen einen zwei Wochen alten Branch gearbeitet
(`PHASE 4`) und daraus geschlossen, mahoraga habe keine ausführbare Zeile. Der lebende Stand
steht bei `PHASE 17 LOCKED`, hat 236 Dateien und **sechs Rechner**, die seit Stufe 1.2 wirklich
gaten (`eigentest_gate()` sucht jedes `bestanden: false`, `main()` gibt 1 zurück). Das
Testkommando dieses Repos sind diese sechs — nicht DevOS' generischer Registerprüfer.

Zwei weitere Annahmen fielen mit: `R-213` ist auf dem lebenden Stand **ein echtes Risiko dieses
Repos** (genau die Falschbestätigung, gegen die DevOS gebaut ist), nicht eine fremde Assay-ID.
Und der geplante `STATE.md`-Generator existiert dort bereits als Prototyp und ist in der
Reihenfolge als Stufe 4.3 geführt.

**Deshalb liegt jetzt keine mahoraga-Task von mir dort.** Das Projekt hat sich am 2026-09-15
eine Reihenfolge gegeben (`workshop/VORGEHEN-NACH-ASSAY.md`); DevOS ist darin **Stufe 5 — Der
zweite Prüfer**, mit derselben Rollentabelle, die DevOS implementiert. Stufe 1 ist vollzogen,
und die nächste offene Stufe sind **drei Auftraggeber-Beschlüsse**. Eine Task daran vorbei zu
schneiden hieße, die Reihenfolge zu unterlaufen, die dieses Projekt sich gerade gegeben hat.

Was heute ohne Beschluss geht: ein **Fremdblick auf einen vorhandenen Stand** — er erzeugt
Befunde, keinen Commit. Der Handover benennt die Lücke selbst (§9.2): *„Wer ist der ‚zweite
Autor'? … in einem Facilitator-Lauf nicht lösbar."*

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

## 5 · Graphify — angebunden, aber am Original verifiziert

Graphify ist ein **remote MCP-Server** (`https://api.graphify.net/mcp`, Bearer-Auth). Die
Anbindung ist gebaut: `tools/mcp_client.py` (Streamable HTTP, stdlib only) und
`tools/graphify_adapter.py`.

**Was ich nicht konnte:** den Dienst tatsächlich fragen. Der Proxy dieser Umgebung lehnt
`api.graphify.net:443` ab (`connect_rejected: policy denial`) — dieselbe Sperre wie bei
OpenAI. Die Werkzeugnamen und Eingabeschemata von Graphify sind mir deshalb **unbekannt**,
und ich habe sie nicht geraten. Stattdessen entdeckt der Adapter sie zur Laufzeit
(`tools/list`) und `devos graphify probe` zeigt sie im Klartext, samt der Angabe, welches
Werkzeug er wählen würde und mit welchen Argumenten. Das ist der erste Befehl nach dem
Eintragen des Graphify-Schlüssels.

**Der eigentliche Entwurf ist die umgekehrte Beweislast.** Graphify kann nicht wissen,
welchen Commit du prüfst — sein Index ist ein eigener Stand. Also wird ihm nicht geglaubt:

```
Graphify schlägt vor  →  der Adapter sucht den Vorschlag im Repo bei der
                         GEPRÜFTEN Revision  →  nur was er dort findet, wird
                         zum Treffer, mit exakter Zeile
```

Drei Folgen, und alle drei sind der Punkt: ein veralteter Graphify-Index kann nichts
durchschmuggeln; das Ausgabeformat darf sich ändern, ohne dass etwas bricht; und ein Treffer
ist nie eine Behauptung des Dienstes, sondern eine Stelle im Original.

Daneben bleibt die **eingebaute Kontextsuche** (`tools/context_index.py`). Sie füllt die
Stelle ohne jeden externen Dienst aus — gegen die echten mahoraga-Register: 290 IDs, 384
Kanten. Der Ablauf braucht auf keiner Stufe ein externes Produkt.

`tools/context_index.py` — revisionsgebunden aus `git show <rev>:<pfad>` gebaut, nie aus dem
Arbeitsverzeichnis. Gegen die echten mahoraga-Register: 290 IDs, 384 Kanten, 23 von 24
Dateien indiziert, die eine ausgelassene benannt.

Die fünf Regeln aus dem Auftrag sind Code, nicht Absicht — mit der jeweiligen Probe:

| Regel | Wie sie durchgesetzt wird | Probe |
|---|---|---|
| Jeder Treffer nennt Revision und Originalstelle | Treffer ohne `rev`/`path`/`line` werden **verworfen**, gezählt und in `omitted` gemeldet — auch die eines externen Anbieters | `X1`, `X3` |
| Treffer aus einer **anderen** Revision | verworfen, nicht benutzt | `X4` |
| Veralteter Index erkennbar | `status` vergleicht Indexrevision mit der geprüften und nennt die Dateien dazwischen; ein veralteter Index wird **nicht benutzt** und steht in der Weglass-Liste | `X2` |
| Der Graph begrenzt den Prüfumfang nicht | Diff, Bestandsliste und Nachforderungsrecht entstehen unabhängig; eine Probe vergleicht `diff_included` mit und ohne Index | `X7` |
| `satisfies → AC-1` ist eine Behauptung | Das Werkzeug leitet **selbst nie** eine Beziehung ab. Steht so etwas im Text, wird der Treffer als `claim` geführt und im Paket als *unbelegt* ausgewiesen | `X1` |
| Fällt er weg, läuft alles weiter | Kein Index, kaputter Anbieter, Anbieter meldet sich veraltet — das Paket entsteht unverändert | `X5`, `X9` |

**Der Anbietervertrag** steht in `tools/context_index.py`: stdin
`{"op":"query","rev":…,"ids":[…],"depth":…}`, stdout
`{"source":…,"built_for_rev":…,"stale":…,"hits":[{"id","path","line","rev"}]}`.
Graphify tritt über `DEVOS_GRAPH_CMD` an diese Stelle — ohne eine Zeile im Verfahren zu
ändern. Proben `Y1`–`Y11` fahren den Adapter gegen einen lokalen MCP-Server: unbekannter
Pfad verworfen, Schnipsel aus älterer Fassung verworfen, reine Prosa ergibt null Treffer
statt eines Fehlers, SSE verstanden, Sitzungskopf mitgeführt, Schlüssel nie in der Ausgabe —
auch nicht im Fehlerfall.

**Die Messung mit und ohne** ist vorbereitet: `--graph off` erzeugt den Vergleichslauf,
`run_state.json` hält je Runde fest, ob Hinweise benutzt wurden, `delivery_metrics` stellt
beide Gruppen nebeneinander — und sagt „n ist klein, das ist ein Hinweis, keine Aussage",
solange es so ist. Der Indexaufbau läuft in jedem Lauf mit und steckt damit in der
gemessenen Durchlaufzeit.

**Was weiterhin offen ist:** ob Graphify mehr findet als der eingebaute Index — und ob sich
der Unterschied in den gemessenen Größen zeigt. Das entscheidet der Vergleich über 3 – 5
Tasks, nicht eine Meinung. Und ob der Adapter Graphifys tatsächliches Antwortformat trifft:
er ist bewusst großzügig beim Einsammeln und streng beim Verifizieren, aber gesehen habe ich
dieses Format nie. `devos graphify probe` ist der Moment, in dem sich das klärt.
