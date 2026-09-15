#!/usr/bin/env python3
"""Ist alles da, um wirklich zu laufen? — ein Kommando, eine Liste, ein Exit-Code.

    devos preflight                      # alles Pruefbare ohne einen Modellaufruf
    devos preflight --live               # zusaetzlich EIN echter Reviewer-Aufruf
    devos preflight --smoke              # zusaetzlich ein ganzer Lauf in einem Wegwerf-Repo
    devos preflight --root ~/mahoraga    # fuer ein bestimmtes Projekt

Jede Zeile sagt nicht nur, ob etwas fehlt, sondern **was genau zu tun ist**.
Ein Bereitschaftstest, den man interpretieren muss, ist keiner.

Ein Punkt ist BLOCKIEREND, wenn ohne ihn kein echter Lauf zustande kommt.
Alles andere ist ein Hinweis und faerbt den Exit-Code nicht.

Exit 0 = bereit · 1 = mindestens ein blockierender Punkt.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEVOS = HERE.parent
sys.path.insert(0, str(HERE))
import devos_env                       # noqa: E402
import model_family as MF              # noqa: E402
from project_config import Config      # noqa: E402

OK, FEHLT, KAPUTT, HINWEIS = "ok", "FEHLT", "KAPUTT", "hinweis"
ZEICHEN = {OK: "ok   ", FEHLT: "FEHLT", KAPUTT: "KAPUTT", HINWEIS: "hinw."}


class Bericht:
    def __init__(self) -> None:
        self.zeilen: list[dict] = []

    def add(self, punkt: str, zustand: str, detail: str = "", fix: str = "",
            blockierend: bool = True) -> None:
        self.zeilen.append({"punkt": punkt, "zustand": zustand, "detail": detail,
                            "fix": fix, "blockierend": blockierend and zustand in (FEHLT, KAPUTT)})

    @property
    def blocker(self) -> list[dict]:
        return [z for z in self.zeilen if z["blockierend"]]


def pruefe_grundlage(b: Bericht) -> None:
    v = sys.version_info
    b.add("Python 3.9+", OK if v >= (3, 9) else KAPUTT, f"{v.major}.{v.minor}.{v.micro}",
          "Neueres Python installieren")
    g = shutil.which("git")
    if not g:
        b.add("git", FEHLT, "nicht im PATH", "git installieren")
    else:
        ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        b.add("git", OK, ver)


def pruefe_umgebung(b: Bericht) -> dict:
    ladung = devos_env.laden()
    if ladung["loaded"]:
        n_datei, n_neu = len(ladung["present"]), len(ladung["set"])
        b.add("Zugangsdatei", OK,
              f"{ladung['path']} — {n_datei} Variable(n)"
              + (f", davon {n_neu} neu gesetzt" if n_neu else
                 ", alle bereits in der Umgebung (die gewinnt)"))
    else:
        b.add("Zugangsdatei", HINWEIS, f"keine unter {ladung['path']}",
              f"mkdir -p ~/.config/devos && cp {DEVOS}/templates/env.example "
              f"{ladung['path']} && chmod 600 {ladung['path']}", blockierend=False)
    for w in ladung["warnings"]:
        b.add("Zugangsdatei", HINWEIS, w, "", blockierend=False)

    key = os.environ.get("DEVOS_REVIEWER_API_KEY")
    b.add("Reviewer-Schluessel", OK if key else FEHLT,
          f"gesetzt, {len(key)} Zeichen" if key else "DEVOS_REVIEWER_API_KEY leer",
          "Schluessel in ~/.config/devos/env eintragen — DAS ist der eine fehlende Posten")

    overrides = {}
    rev = MF.aus_umgebung("REVIEWER", overrides)
    bui = MF.aus_umgebung("BUILDER", overrides)
    for rolle, d, var in (("Reviewer-Modell", rev, "DEVOS_REVIEWER_MODEL"),
                          ("Builder-Modell", bui, "DEVOS_BUILDER_MODEL")):
        if not d["model"]:
            b.add(rolle, FEHLT, f"{var} nicht gesetzt", f"{var}=… in ~/.config/devos/env")
        elif not d["family"]:
            b.add(rolle, KAPUTT, f"{d['model']} — Familie unbestimmbar ({d['declared_via']})",
                  f"{var.replace('_MODEL', '_FAMILY')}=… setzen; unbekannt gilt nicht als "
                  "verschieden, und das Gate laesst dann kein PASS zu")
        else:
            b.add(rolle, OK, f"{d['model']} → {d['family']}")

    t = MF.trennung(bui, rev)
    if t["separated"] is True:
        b.add("Familientrennung", OK, t["why"])
    elif t["separated"] is False:
        b.add("Familientrennung", KAPUTT, t["why"],
              "DEVOS_REVIEWER_MODEL auf ein Modell einer anderen Familie setzen — "
              "der Transport verweigert den Versand sonst")
    else:
        b.add("Familientrennung", FEHLT, t["why"], "Beide Modellnamen bzw. -familien setzen")

    if os.environ.get("DEVOS_REVIEWER_PRICE_IN") and os.environ.get("DEVOS_REVIEWER_PRICE_OUT"):
        b.add("Token-Preise", OK, "gesetzt — Kostenlimits binden")
    else:
        b.add("Token-Preise", HINWEIS, "DEVOS_REVIEWER_PRICE_IN/OUT nicht gesetzt",
              "Ohne sie werden Token gezaehlt, aber kein Kostenlimit bindet", blockierend=False)

    cmd = os.environ.get("DEVOS_BUILDER_CMD")
    if cmd:
        b.add("Builder-Kommando", OK, cmd[:70])
    else:
        b.add("Builder-Kommando", HINWEIS, "DEVOS_BUILDER_CMD nicht gesetzt",
              "Ohne ihn haelt der Lauf nach dem BUILD-BRIEF an und du lieferst den Commit "
              "selbst. Das ist ein gueltiger Betrieb, nur kein automatischer",
              blockierend=False)
    return {"reviewer": rev, "builder": bui, "separated": t["separated"]}


def pruefe_projekt(b: Bericht, root: str, tests: str | None) -> Config:
    cfg = Config(root)
    rc = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        b.add("Projekt-Repo", KAPUTT, f"{root} ist kein git-Repo mit Commits",
              "Repo klonen bzw. einen ersten Commit anlegen")
        return cfg
    b.add("Projekt-Repo", OK, f"{root} @ {rc.stdout.strip()[:12]}")

    if cfg.fehler:
        b.add("Projektbindung", KAPUTT, cfg.fehler, ".devos.json reparieren")
    elif not cfg.vorhanden:
        b.add("Projektbindung", FEHLT, f"keine .devos.json in {root}",
              f"cp {DEVOS}/templates/devos.json.example {root}/.devos.json und anpassen")
    elif not cfg.registers:
        b.add("Projektbindung", HINWEIS, "keine `registers` — keine Normquellen im Paket",
              "registers in .devos.json eintragen", blockierend=False)
    else:
        fehlend = [f"{k} → {v.get('path')}" for k, v in cfg.registers.items()
                   if not (Path(root) / (v.get("path") or "")).exists()]
        if fehlend:
            b.add("Register", KAPUTT, "nicht vorhanden: " + ", ".join(fehlend),
                  "Pfade in .devos.json richtigstellen")
        else:
            b.add("Register", OK, f"{len(cfg.registers)} Register, alle vorhanden")

    # Worktree: ohne ihn laufen die Tests nicht isoliert, und das Gate haelt das an
    wt = Path(tempfile.mkdtemp(prefix="devos-pf-"))
    try:
        r = subprocess.run(["git", "-C", root, "worktree", "add", "--detach", str(wt), "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            b.add("Isolierter Worktree", OK, "git worktree add funktioniert")
        else:
            b.add("Isolierter Worktree", KAPUTT, r.stderr.strip()[:120],
                  "Ohne ihn laufen die Tests nicht isoliert und das Gate schliesst PASS aus")
    finally:
        subprocess.run(["git", "-C", root, "worktree", "remove", "--force", str(wt)],
                       capture_output=True, text=True)
        shutil.rmtree(wt, ignore_errors=True)

    # Testkommando: ohne einen gruenen Lauf endet JEDE Lieferung bei CHANGES_REQUIRED
    if not tests:
        b.add("Testkommando", FEHLT, "--tests nicht angegeben",
              "Ohne Testnachweis ist `tests.ran=false` und das Gate schliesst PASS aus — "
              "JEDE Lieferung dieses Repos endet bei CHANGES_REQUIRED. Fuer reine "
              f"Dokumenten-Repos: --tests \"python3 {DEVOS}/tools/check_registers.py --root .\"")
    else:
        p = subprocess.run(["bash", "-lc", tests], cwd=root, capture_output=True, text=True)
        if p.returncode == 0:
            b.add("Testkommando", OK, f"`{tests[:50]}` → Exit 0")
        else:
            b.add("Testkommando", KAPUTT,
                  f"`{tests[:50]}` → Exit {p.returncode}: "
                  f"{(p.stdout + p.stderr).strip().splitlines()[-1][:90] if (p.stdout + p.stderr).strip() else ''}",
                  "Erst gruen bekommen. Ein roter Test laesst den Reviewer gar nicht erst rufen")

    # Laufmaterial darf nicht versehentlich mitcommittet werden
    gi = Path(root) / ".gitignore"
    txt = gi.read_text(encoding="utf-8") if gi.exists() else ""
    fehlt = [x for x in ("work/review/", "work/runs/") if x not in txt]
    if fehlt:
        b.add("Laufmaterial ignoriert", HINWEIS, "nicht in .gitignore: " + ", ".join(fehlt),
              f"Diese Zeilen in {gi} eintragen — Review-Pakete tragen den vollen Diff",
              blockierend=False)
    else:
        b.add("Laufmaterial ignoriert", OK, "work/review/ und work/runs/ sind ausgenommen")

    # Graphify: eine Erweiterung. Sie blockiert nie — auch dann nicht, wenn sie
    # kaputt ist. Ein Kontextabruf, der einen Lauf verhindern kann, waere keine
    # Erweiterung mehr, sondern eine Abhaengigkeit.
    gkey = os.environ.get("DEVOS_GRAPHIFY_KEY") or os.environ.get("GRAPHIFY_API_KEY")
    if not gkey:
        b.add("Graphify (MCP)", HINWEIS, "kein Schluessel — der eingebaute Index wird benutzt",
              f"DEVOS_GRAPHIFY_KEY=… in ~/.config/devos/env, dann `devos graphify probe`",
              blockierend=False)
    else:
        p = subprocess.run([sys.executable, str(HERE / "graphify_adapter.py"), "probe"],
                           capture_output=True, text=True, timeout=90)
        erste = (p.stdout.strip().splitlines() or [""])[0]
        b.add("Graphify (MCP)", OK if p.returncode == 0 else HINWEIS,
              erste[:90] if p.returncode == 0 else
              f"nicht erreichbar: {(p.stderr or p.stdout).strip()[:90]}",
              "`devos graphify probe` zeigt Werkzeuge und Schemata im Klartext",
              blockierend=False)

    # Kontextindex: reine Information, blockiert nie
    p = subprocess.run([sys.executable, str(HERE / "context_index.py"), "status",
                        "--root", root, "--rev", "HEAD"], capture_output=True, text=True)
    zustand = "aktuell" if p.returncode == 0 else ("veraltet/fehlt")
    b.add("Kontextindex", HINWEIS if p.returncode else OK,
          f"{zustand} — {p.stdout.strip().splitlines()[-1][:80] if p.stdout.strip() else ''}",
          f"python3 {HERE}/context_index.py build --root {root}  (optional — der Lauf "
          "baut ihn ohnehin vor jedem Paket neu)", blockierend=False)
    return cfg


def pruefe_live(b: Bericht) -> None:
    """EIN echter Aufruf. Bis hierher ist alles nur Konfiguration."""
    import urllib.error
    import urllib.request
    key = os.environ.get("DEVOS_REVIEWER_API_KEY")
    model = os.environ.get("DEVOS_REVIEWER_MODEL")
    base = os.environ.get("DEVOS_REVIEWER_BASE_URL", "https://api.openai.com/v1")
    if not key or not model:
        b.add("Reviewer erreichbar", FEHLT, "ohne Schluessel und Modell kein Live-Test",
              "Erst die Konfiguration, dann --live")
        return
    koerper = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": "Antworte mit genau einem JSON-Objekt."},
                     {"role": "user", "content": 'Antworte exakt: {"bereit": true}'}],
        "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=koerper,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            p = json.loads(r.read().decode())
    except urllib.error.HTTPError as ex:
        leib = ex.read().decode("utf-8", "replace")[:200]
        b.add("Reviewer erreichbar", KAPUTT, f"HTTP {ex.code}: {leib}",
              "401/403 → Schluessel falsch · 404 → Modellname falsch · "
              "429 → Kontingent · Proxy → Netzweg pruefen")
        return
    except Exception as ex:
        b.add("Reviewer erreichbar", KAPUTT, f"{type(ex).__name__}: {str(ex)[:140]}",
              "Netzweg zum Endpunkt pruefen (Proxy, Firewall, Basis-URL)")
        return
    try:
        inhalt = p["choices"][0]["message"]["content"]
        json.loads(inhalt)
        u = p.get("usage") or {}
        b.add("Reviewer erreichbar", OK,
              f"{model} antwortet gueltiges JSON"
              + (f" · {u.get('prompt_tokens')}+{u.get('completion_tokens')} Token" if u else ""))
        if not u:
            b.add("Token-Zaehlung", HINWEIS, "der Endpunkt liefert kein `usage` — "
                  "Kosten bleiben unberechenbar", "", blockierend=False)
    except Exception:
        b.add("Reviewer erreichbar", KAPUTT, "Antwort ist kein gueltiges JSON-Objekt",
              "Ein Modell, das `response_format: json_object` nicht einhaelt, ist als "
              "Reviewer nicht brauchbar — ein anderes waehlen")


def rauchprobe(b: Bericht) -> None:
    """Ein ganzer Lauf in einem Wegwerf-Repo, gegen den ECHTEN Reviewer."""
    td = tempfile.mkdtemp(prefix="devos-smoke-")
    try:
        def git(*a):
            return subprocess.run(["git", "-C", td, *a], capture_output=True, text=True)
        git("init", "-q", ".")
        git("config", "user.email", "devos@local"); git("config", "user.name", "devos")
        (Path(td) / ".devos.json").write_text(json.dumps({"project": "devos-rauchprobe"}),
                                              encoding="utf-8")
        (Path(td) / "rechnen.py").write_text("def verdopple(x):\n    return x + x\n",
                                             encoding="utf-8")
        (Path(td) / "test_rechnen.py").write_text(
            "from rechnen import verdopple\nassert verdopple(3) == 6\nprint('ok')\n",
            encoding="utf-8")
        git("add", "."); git("commit", "-qm", "basis")
        base = git("rev-parse", "HEAD").stdout.strip()
        (Path(td) / "rechnen.py").write_text(
            "def verdopple(x):\n    return x * 2\n", encoding="utf-8")
        git("add", "."); git("commit", "-qm", "verdopple ueber Multiplikation")
        (Path(td) / "TASK-RAUCH.md").write_text(
            "# TASK-RAUCH — verdopple ueber Multiplikation\n\n## Acceptance\n"
            "- verdopple(x) benutzt Multiplikation statt Addition\n"
            "- der vorhandene Test bleibt gruen\n\n## Forbidden\n"
            "- keine neue Abhaengigkeit\n", encoding="utf-8")
        p = subprocess.run([sys.executable, str(HERE / "run_task.py"),
                            "--task", str(Path(td) / "TASK-RAUCH.md"), "--root", td,
                            "--base", base, "--tests", "python3 test_rechnen.py",
                            "--no-builder", "--max-rounds", "0"],
                           capture_output=True, text=True)
        letzte = [x for x in p.stdout.strip().splitlines() if x.strip()][-3:]
        if p.returncode == 0:
            b.add("Rauchprobe (ganzer Lauf)", OK,
                  "Paket, echtes Reviewer-Urteil, Gate: MERGEABLE_PENDING_HUMAN")
        elif p.returncode in (1, 2):
            b.add("Rauchprobe (ganzer Lauf)", HINWEIS,
                  f"Lauf vollstaendig, Gate sagte Exit {p.returncode}: {' | '.join(letzte)[:200]}",
                  "Die Kette funktioniert. Ob das Urteil richtig ist, entscheidest du — "
                  "genau dafuer ist das Gate da", blockierend=False)
        else:
            b.add("Rauchprobe (ganzer Lauf)", KAPUTT,
                  f"Exit {p.returncode}: {(p.stderr or p.stdout).strip()[-250:]}",
                  "Die obigen Punkte zuerst abarbeiten")
        hv = Path(td) / "work" / "runs" / "TASK-RAUCH" / "HANDOVER.md"
        b.add("Entscheidungsblatt", OK if hv.exists() else KAPUTT,
              "erzeugt" if hv.exists() else "nicht erzeugt", "")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bereitschaft pruefen — was fehlt noch?")
    ap.add_argument("--root", default=".")
    ap.add_argument("--tests", default=None)
    ap.add_argument("--live", action="store_true", help="EIN echter Reviewer-Aufruf")
    ap.add_argument("--smoke", action="store_true", help="ein ganzer Lauf, impliziert --live")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = str(Path(a.root).resolve())
    b = Bericht()
    pruefe_grundlage(b)
    pruefe_umgebung(b)
    pruefe_projekt(b, root, a.tests)
    if a.live or a.smoke:
        pruefe_live(b)
    if a.smoke and not b.blocker:
        rauchprobe(b)
    elif a.smoke:
        b.add("Rauchprobe (ganzer Lauf)", HINWEIS,
              "uebersprungen — erst die blockierenden Punkte oben", "", blockierend=False)

    if a.json:
        print(json.dumps({"ready": not b.blocker, "checks": b.zeilen}, ensure_ascii=False, indent=2))
        return 1 if b.blocker else 0

    print(f"DevOS Bereitschaft · Projekt {root}\n")
    for z in b.zeilen:
        print(f"  {ZEICHEN[z['zustand']]}  {z['punkt']:<26} {z['detail']}")
        if z["zustand"] in (FEHLT, KAPUTT) and z["fix"]:
            for zeile in _umbruch(z["fix"], 84):
                print(f"         → {zeile}")
    print()
    if not b.blocker:
        print("BEREIT. Nichts blockiert einen echten Lauf.")
        if not (a.live or a.smoke):
            print("Der naechste Schritt ist der einzige, der Geld kostet:\n"
                  "    devos preflight --smoke")
        return 0
    print(f"NICHT BEREIT — {len(b.blocker)} blockierende(r) Punkt(e):")
    for z in b.blocker:
        print(f"  - {z['punkt']}: {z['detail']}")
    return 1


def _umbruch(text: str, breite: int) -> list[str]:
    worte, zeilen, akt = text.split(), [], ""
    for w in worte:
        if len(akt) + len(w) + 1 > breite:
            zeilen.append(akt)
            akt = w
        else:
            akt = f"{akt} {w}".strip()
    if akt:
        zeilen.append(akt)
    return zeilen


if __name__ == "__main__":
    sys.exit(main())
