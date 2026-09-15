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
cd ~/mahoraga

devos preflight --tests "python3 ~/devos/tools/check_registers.py --root ."
```

`.devos.json` liegt dort schon: vier Register, ihre Formen, die Laufgrenzen, und `R-213`
als auswärtige ID vermerkt. Geprüft — 290 IDs, alle auflösbar.

Dann der erste Lauf:

```bash
devos run \
    --task work/tasks/TASK-001-state-generator.md \
    --onto main \
    --tests "python3 ~/devos/tools/check_registers.py --root ."
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

**Ein echtes Graphify tritt an seine Stelle, sobald es feststeht:**

```
DEVOS_GRAPH_CMD="graphify devos-adapter"
```

Das Kommando bekommt auf stdin `{"op":"query","rev":"<SHA>","ids":[…],"depth":1}` und
antwortet mit `{"source":…,"built_for_rev":…,"stale":…,"hits":[{"id","path","line","rev"}]}`.
Alles, was diesen Vertrag verletzt, wird verworfen und gemeldet — auch und gerade von einem
externen Anbieter. Der Vertrag steht in `tools/context_index.py`; Proben `X3`/`X4` halten
ihn fest.

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

Alles bis auf den Schlüssel ist gebaut und mit **159 Proben** belegt — aber
**in der gesamten Entwicklung wurde kein einziges Mal ein echtes Modell aufgerufen.**
Geprüft ist der Transport, das Gate, der Zustand, die Grenzen und der Index; nicht das
Urteil. Schritt 2 ist genau der Schritt, der das ändert. Was dabei am ehesten hakt, steht
in [`INBETRIEBNAHME.md`](INBETRIEBNAHME.md) unter „Das größte Risiko des ersten echten
Laufs".
