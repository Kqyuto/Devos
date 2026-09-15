# Von hier bis zum ersten echten Lauf

Drei Schritte. Alles andere ist gebaut und geprüft.

```bash
git clone https://github.com/Kqyuto/Devos ~/devos
export PATH="$HOME/devos/bin:$PATH"          # optional, macht `devos` zum Kommando
```

---

## Schritt 1 — den Schlüssel eintragen

```bash
mkdir -p ~/.config/devos
cp ~/devos/templates/env.example ~/.config/devos/env
chmod 600 ~/.config/devos/env
$EDITOR ~/.config/devos/env
```

Auszufüllen ist **eine Zeile**:

```
DEVOS_REVIEWER_API_KEY=sk-...
```

> **Wichtig, weil es leicht verwechselt wird:** gemeint ist ein **API-Schlüssel**
> von `platform.openai.com`. Ein ChatGPT-Abo oder ein ChatGPT-*Connector* ist etwas
> anderes — der gibt einer Oberfläche Zugriff, aber keinem Programm einen Schlüssel.
> DevOS ruft `POST /v1/chat/completions` selbst auf; dafür braucht es `sk-…`.
> Jede OpenAI-kompatible Schnittstelle geht auch (Azure, OpenRouter, ein eigener
> Endpunkt) — dann zusätzlich `DEVOS_REVIEWER_BASE_URL` setzen.

Der Rest der Datei ist vorbelegt (`gpt-5` als Reviewer, `claude-opus-5` als Builder,
Claude Code als Builder-Adapter). Zwei Zeilen lohnen sich trotzdem:

```
DEVOS_REVIEWER_PRICE_IN=…      # USD je 1 Mio. Eingabe-Token
DEVOS_REVIEWER_PRICE_OUT=…     # USD je 1 Mio. Ausgabe-Token
```

Ohne sie werden Token gezählt, aber **kein Kostenlimit bindet** — das Werkzeug sagt das bei
jedem Lauf, statt eine Zahl zu erfinden.

> Die Datei liegt bewusst **außerhalb beider Repositories**. Ein Schlüssel im
> Arbeitsverzeichnis wird irgendwann mitcommittet — nicht aus Nachlässigkeit, sondern weil
> `git add -A` genau dafür gebaut ist.

---

## Schritt 2 — einmal testen

```bash
devos preflight --smoke
```

Das prüft der Reihe nach: Python und git · Zugangsdatei · Schlüssel · beide Modelle ·
**ob die Modellfamilien nachweislich getrennt sind** · Projektbindung · Register ·
isolierter Worktree · Testkommando · `.gitignore` · Kontextindex. Dann ruft es den echten
Reviewer **einmal** an und fährt anschließend einen **vollständigen Lauf** in einem
Wegwerf-Repository: Paket, echtes Urteil, Gate, Entscheidungsblatt.

Jede Zeile, die nicht `ok` ist, sagt **was genau zu tun ist**. Exit 0 heißt bereit.

Was hier schiefgehen kann und was es bedeutet:

| Meldung | Bedeutung |
|---|---|
| `HTTP 401` / `403` | Schlüssel falsch oder nicht freigeschaltet |
| `HTTP 404` | Modellname existiert bei diesem Anbieter nicht |
| `Antwort ist kein gültiges JSON-Objekt` | Das Modell hält `response_format` nicht ein und ist als Reviewer unbrauchbar — ein anderes wählen |
| `Familientrennung VERLETZT` | Reviewer und Builder sind dieselbe Familie. Der Transport sendet dann gar nicht erst |
| `Testkommando … Exit 1` | Erst grün bekommen. Ein roter Test lässt den Reviewer gar nicht erst rufen |
| `n Acceptance-Punkt(e) unbewertet — eine Nachfrage` | Kein Fehler. Der Reviewer hat nicht alle Punkte beurteilt und wird **einmal** danach gefragt. Bleibt er unvollständig, entscheidet das Gate |

Die Rauchprobe kostet ein paar Cent. Sie ist der einzige Punkt in dieser Anleitung, an dem
Geld fließt — und der einzige, der beweist, dass ein **echtes** Modell mitspielt.

---

## Schritt 3 — mahoraga anschließen

```bash
git clone https://github.com/Kqyuto/mahoraga ~/mahoraga
cd ~/mahoraga && git checkout claude/devos-mahoraga-workflow-cd7bqv

devos preflight
```

Kein `--tests` nötig: `.devos.json` liegt dort und nennt das Testkommando selbst — die
**sechs Pflichtläufe des Projekts** (`review_rechnung1.py`, `state_registerstand.py`,
`fremdrechnung.py`, `baugraph_rechnung1.py`, `zitat_rechnung1.py`, `tprime_rechnung1.py`).
Nicht DevOS' eigener Registerprüfer: der kennt nur `.devos.json`, die sechs kennen die
Regeln dieses Bestands. Geprüft — 702 IDs, alle auflösbar, Testlauf grün in 1,3 s.

Dann ein Lauf. **Aber welcher**, das steht in
[`work/README.md`](https://github.com/Kqyuto/mahoraga/blob/claude/devos-mahoraga-workflow-cd7bqv/work/README.md)
des Projekts — und die Antwort ist absichtlich zurückhaltend: mahoraga hat sich gerade eine
Reihenfolge gegeben (`workshop/VORGEHEN-NACH-ASSAY.md`), DevOS ist darin **Stufe 5**, und
davor stehen drei Beschlüsse, die nur der Auftraggeber fällen kann. Was heute ohne Beschluss
geht, ist ein **Fremdblick auf einen bereits vorhandenen Stand** — er ändert nichts:

```bash
devos review --task work/tasks/<eine-task>.md --base <commit-vorher> --head HEAD
```

Der Lauf baut den Kontextindex, ruft den Builder, fährt die Maschinentests im isolierten
Worktree, schnürt das Paket, holt das Urteil, rechnet das Gate — und legt das
Entscheidungsblatt nach `work/runs/TASK-001/HANDOVER.md`.

**Dort hört die Automatik auf.** `PASS` heißt: *du darfst jetzt entscheiden.* Kein Programm
dieser Kette mergt, checkt aus oder verschiebt einen Branch.

---

## Was zwischen Schritt 2 und 3 gehört

**Pilot 1 ist DevOS selbst**, nicht mahoraga:

```bash
cd ~/devos
devos run --task work/tasks/TASK-001-devos-gate-korrektur.md \
          --onto main --tests "python3 tools/selftest.py"
```

Befund `G06`: das Gate liest den Nachweis der Familientrennung aus `review_dispatch.json`
und prüft diese Datei gegen nichts. Er ist **absichtlich offen** — klein, von außen prüfbar,
und er betrifft genau das, worauf das Verfahren beruht. Ein erster Lauf an einem Gegenstand,
dessen richtiges Ergebnis man kennt, ist mehr wert als einer an einem, bei dem man es raten
muss.

---

## Der Builder

Vorbelegt ist Claude Code (`tools/builders/claude_code.sh`). Er bekommt den `BUILD-BRIEF.md`
der Runde und muss einen Commit hinterlassen. Committet er nicht selbst, holt der Adapter es
nach — **und schreibt in die Commit-Nachricht, dass der Builder es nicht getan hat.**
Stillschweigend nachbessern wäre das Gegenteil dessen, wofür das Verfahren gebaut ist.

Ohne `DEVOS_BUILDER_CMD` läuft alles genauso, nur hält der Lauf nach dem Brief an und du
lieferst den Commit selbst. Derselbe Aufruf setzt ihn dann fort — er wiederholt nichts.

---

## Der Kontextindex (die Graphify-Stelle)

Läuft schon, eingebaut, ohne Zutun:

```bash
devos index build  --root ~/mahoraga      # macht `devos run` vor jedem Paket selbst
devos index status --root ~/mahoraga      # Exit 1, wenn veraltet
devos index query  --root ~/mahoraga --ids D-001,OQ-004
```

Er findet Fundstellen zu jeder referenzierten ID und legt sie dem Reviewer als **Zeiger**
bei. Fünf Regeln sind dabei Code, nicht Absicht:

1. Jeder Treffer nennt **Revision und Fundstelle**. Ein Treffer ohne beides wird verworfen,
   gezählt und gemeldet.
2. Ein **veralteter Index wird nicht benutzt** — er steht stattdessen in der Weglass-Liste.
3. Der Index **begrenzt den Prüfumfang nicht**: Diff, Bestandsliste und das Recht des
   Reviewers, jede Datei nachzufordern, bleiben unangetastet.
4. Eine im Text behauptete Beziehung (`satisfies → AC-1`) wird als **Behauptung** geführt,
   nie als Nachweis. Das Werkzeug leitet selbst keine ab.
5. Fällt er ganz weg, läuft das Verfahren unverändert weiter.

### Graphify anschließen

Graphify ist ein **MCP-Server**, kein Kommandozeilenwerkzeug. Der Adapter dafür ist gebaut:

```
DEVOS_GRAPHIFY_KEY=...
DEVOS_GRAPHIFY_URL=https://api.graphify.net/mcp
DEVOS_GRAPH_CMD=python3 $DEVOS_HOME/tools/graphify_adapter.py
```

Dann **zuerst**:

```bash
devos graphify probe
```

Das fragt den Server, welche Werkzeuge er hat, und zeigt deren Eingabeschemata im Klartext —
samt der Angabe, welches der Adapter wählen würde und mit welchen Argumenten. Ich konnte das
nicht vorwegnehmen: `api.graphify.net` ist aus dieser Umgebung nicht erreichbar
(`connect_rejected: policy denial`), und **einen Werkzeugnamen zu raten wäre genau die Sorte
Annahme, die dieses Verfahren sonst überall verbietet.** Passt die Wahl nicht:
`DEVOS_GRAPHIFY_TOOL=<name>`, fehlen Pflichtfelder: `DEVOS_GRAPHIFY_ARGS={"feld":"wert"}` —
`probe` sagt beides wörtlich an.

**Wie der Adapter Graphifys Antworten behandelt**, und das ist der eigentliche Punkt:

```
Graphify schlägt vor  →  der Adapter sucht den Vorschlag im Repo
                         bei der GEPRÜFTEN Revision  →  nur was er dort findet,
                         wird zum Treffer, mit exakter Zeile
```

Graphify kann gar nicht wissen, welchen Commit du gerade prüfst. Also wird ihm nicht
geglaubt: jeder Vorschlag wird gegen `git show <rev>:<pfad>` nachgeschlagen. Was dort nicht
steht, wird **nicht geliefert**, sondern als *nicht auffindbar* gemeldet. Ein veralteter
Graphify-Index kann damit nichts durchschmuggeln, und das Ausgabeformat darf sich ändern,
ohne dass etwas bricht — entschieden wird am Original.

Proben `Y1`–`Y11` gegen einen lokalen MCP-Server halten das fest, darunter: unbekannter Pfad
wird verworfen, Schnipsel aus einer älteren Fassung wird verworfen, reine Prosa ergibt null
Treffer statt eines Fehlers, SSE-Antworten werden verstanden, und der Schlüssel taucht in
keiner Ausgabe auf — auch nicht im Fehlerfall.

---

## Messen, sobald Tasks laufen

```bash
devos metrics --from-run work/runs >> work/deliveries.jsonl
devos metrics --log work/deliveries.jsonl
```

Der erste Befehl erzeugt, was eine Maschine messen kann: Durchlaufzeit, Korrekturrunden,
Blocker, Token, Kosten, ob mit oder ohne Kontextabruf gearbeitet wurde. Von Hand bleiben
vier Zeilen je Lieferung, und jede aus einem Grund:

| Zeile | Warum keine Maschine sie schreiben darf |
|---|---|
| `human_minutes` | Nur du weißt sie. Nicht gebucht heißt **unbekannt**, nicht null — sonst läse sich die ungemessene Lieferung als die beste der Reihe |
| `accepted` | Erst beim Merge. `PASS` ist keine Abnahme |
| `false_alarms` | Ein Urteil über ein Urteil |
| `escaped_defect` | Nachträglich gefundene Fehler — die Zahl, die entscheidet, ob das Gate etwas taugt |

Nach drei vollständig gemessenen Lieferungen rechnet das Werkzeug Menschenanteil und
Durchsatz gegen 6 – 14 h/Woche. Bei weniger sagt es das und urteilt nicht.

---

## Der Stand in einem Satz

Alles bis auf den Schlüssel ist gebaut und mit **182 Proben** belegt — aber
**in der gesamten Entwicklung wurde kein einziges Mal ein echtes Modell aufgerufen.**
Geprüft ist der Transport, das Gate, der Zustand, die Grenzen und der Index; nicht das
Urteil. Schritt 2 ist genau der Schritt, der das ändert. Was dabei am ehesten hakt, steht
in [`INBETRIEBNAHME.md`](INBETRIEBNAHME.md) unter „Das größte Risiko des ersten echten
Laufs".
