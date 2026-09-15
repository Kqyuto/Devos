#!/usr/bin/env python3
"""Reproduktion der Reviewer-Befunde G01-G05 — ausfuehrbar, nicht behauptet.

GPT hat bei einer Codelektuere vier moegliche Luecken benannt. Eine Lektuere ist
keine Reproduktion. Dieses Skript fuehrt jede Luecke am *laufenden* Werkzeug vor:
es baut die Eingabe, ruft das echte Gate bzw. den echten Transport auf und liest
den Exit-Code. Eine Luecke, die sich so nicht vorfuehren laesst, wird als
WIDERLEGT gefuehrt und nicht korrigiert.

Aufruf:

    python3 tools/reproduce_findings.py          # Tabelle, Exit 1 solange offen
    python3 tools/reproduce_findings.py --json

Exit 0 = kein Befund mehr vorfuehrbar (alle korrigiert).
Exit 1 = mindestens ein Befund am aktuellen Stand vorfuehrbar.

Das Skript bleibt nach der Korrektur im Repo. Es ist der Beleg dafuer, dass die
Korrektur den vorgefuehrten Fall wirklich trifft — und faellt auf 1 zurueck,
wenn jemand sie spaeter wieder herausnimmt.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT, DISPATCH = HERE / "review_result.py", HERE / "review_dispatch.py"
DISPATCH_FLAG = "--dispatch" in subprocess.run(
    [sys.executable, str(RESULT), "--help"], capture_output=True, text=True).stdout

HEAD = "4cbc9864e197aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BASE = "1111111111111111111111111111111111111111"
FREMD = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def result(**over) -> dict:
    r = {"schema_version": "1.0", "task_id": "TASK-000",
         "reviewed_range": {"base": BASE, "head": HEAD},
         "status": "PASS", "context_sufficient": True, "blocking": [], "non_blocking": [],
         "reviewed": {"files": ["x.py"],
                      "acceptance_items": [{"item": "A1 muss gelten", "verdict": "met"}],
                      "tests": {"judged": "adequate"}}}
    r.update(over)
    return r


def context(**over) -> dict:
    c = {"schema_version": "1.1", "generated_at": "2026-09-15T00:00:00+00:00",
         "generated_by": "probe", "project": "probe",
         "task": {"id": "TASK-000", "acceptance": ["A1 muss gelten"], "forbidden": [], "decisions": []},
         "range": {"base": BASE, "head": HEAD, "commits": []},
         "files_changed": [], "diff_included": [], "omitted": [], "referenced_ids": [],
         "norm_sources": {},
         "tests": {"ran": True, "passed": True, "isolated": True, "exit_code": 0, "ran_against": HEAD},
         "builder": {"model": "claude-opus-5", "family": "anthropic", "declared_via": "probe"},
         "counts": {"files": 0, "diff_bytes": 0, "omitted": 0, "commits": 0}}
    c.update(over)
    return c


def dispatch_prov(**over) -> dict:
    d = {"schema_version": "1.1", "model": "gpt-5",
         "reviewer": {"model": "gpt-5", "family": "openai", "declared_via": "probe"},
         "builder": {"model": "claude-opus-5", "family": "anthropic", "declared_via": "probe"},
         "independence": {"separated": True, "why": "openai != anthropic"}, "ok": True}
    d.update(over)
    return d


def gate(res, ctx, prov=dispatch_prov(), ctx_raw: str | None = None) -> dict:
    """Ruft das echte Gate.

    Gibt ausdruecklich zurueck, OB das Gate ueberhaupt geurteilt hat. Ein
    Programmabbruch (unbekanntes Argument, Traceback) ist kein Urteil — und darf
    nicht als "Befund behoben" durchgehen, nur weil der Exit-Code zufaellig nicht
    0 ist. Genau diese Verwechslung ist die Fehlerklasse, die hier gesucht wird.
    """
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "review_result.json"
        rp.write_text(json.dumps(res), encoding="utf-8")
        cmd = [sys.executable, str(RESULT), str(rp), "--json"]
        if ctx_raw is not None:
            cp = Path(td) / "review_context.json"
            cp.write_text(ctx_raw, encoding="utf-8")
            cmd += ["--context", str(cp)]
        elif ctx is not None:
            cp = Path(td) / "review_context.json"
            cp.write_text(json.dumps(ctx), encoding="utf-8")
            cmd += ["--context", str(cp)]
        if prov is not None and DISPATCH_FLAG:
            pp = Path(td) / "review_dispatch.json"
            pp.write_text(json.dumps(prov), encoding="utf-8")
            cmd += ["--dispatch", str(pp)]
        p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        grund = ("unbekanntes Argument" if "usage:" in p.stderr else
                 "Absturz" if "Traceback" in p.stderr else "keine JSON-Ausgabe")
        return {"geurteilt": False, "code": p.returncode,
                "warum": f"{grund}: {p.stderr.strip().splitlines()[-1][:90] if p.stderr.strip() else ''}"}
    return {"geurteilt": True, "code": p.returncode, "out": out,
            "spur": json.dumps(out, ensure_ascii=False)}


def fall(name: str, erwartet: str, g: dict, codes: tuple[int, ...], marke: str) -> dict:
    """Ein Fall gilt nur als behoben, wenn das Gate geurteilt UND den Grund benannt hat.

    Der Exit-Code allein reicht nicht: `2` entsteht auch, wenn argparse ein
    unbekanntes Argument ablehnt. Deshalb muss die Begruendung im Ergebnis stehen.
    """
    if not g["geurteilt"]:
        return {"fall": name, "erwartet": erwartet, "defekt": True,
                "beobachtet": f"Gate hat nicht geurteilt — {g['warum']}"}
    ok = g["code"] in codes and marke.lower() in g["spur"].lower()
    grund = ""
    if g["code"] not in codes:
        grund = f"Exit {g['code']}, erwartet {codes}"
    elif marke.lower() not in g["spur"].lower():
        grund = f"Exit {g['code']} richtig, aber der Grund {marke!r} wird nicht benannt"
    return {"fall": name, "erwartet": erwartet, "defekt": not ok,
            "beobachtet": (f"Exit {g['code']} · {g['out'].get('gate')}" if ok else grund)}


def mock_endpoint(antworten: list[str]):
    folge = list(antworten)

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            inhalt = folge.pop(0) if folge else "{}"
            pay = json.dumps({"choices": [{"message": {"content": inhalt}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(pay)))
            self.end_headers()
            self.wfile.write(pay)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/v1", srv.shutdown


# ---------------------------------------------------------------- die Befunde
#
# Jeder Fall nennt die MARKE: die Zeichenkette, mit der das Gate den Grund
# benennen muss. Ein richtiger Exit-Code ohne benannten Grund gilt nicht als
# behoben — sonst faerbt ein zufaellig passender Code den Befund gruen.

def g01_kontext_ohne_schemapruefung() -> list[dict]:
    """review_context.json wird nicht strukturell gegen ein Schema geprueft."""
    return [
        fall("G01.1 Kontext ist eine JSON-Liste statt eines Objekts",
             "Strukturfehler statt Absturz, PASS unerreichbar",
             gate(result(), None, ctx_raw="[]"), (2, 3), "Kontext verletzt"),
        fall("G01.2 tests.passed ist der String \"false\" — in Python wahr",
             "Typfehler wird erkannt, PASS unerreichbar",
             gate(result(), context(tests={"ran": "ja", "passed": "false", "isolated": "nope",
                                           "exit_code": 0, "ran_against": HEAD})),
             (2,), "tests.passed"),
        fall("G01.3 task ist ein String statt eines Objekts",
             "Typfehler wird erkannt, PASS unerreichbar",
             gate(result(), context(task="das ist ein String, kein Objekt")), (2,), ".task"),
    ]


def g02_acceptance_ohne_abdeckung() -> list[dict]:
    """Die bewerteten Acceptance-Punkte werden nicht gegen die geforderten abgeglichen."""
    ctx = context()
    ctx["task"]["acceptance"] = ["A1 muss gelten", "A2 muss gelten", "A3 muss gelten"]

    leer = result()
    leer["reviewed"]["acceptance_items"] = []
    eins = result()
    eins["reviewed"]["acceptance_items"] = [{"item": "A1 muss gelten", "verdict": "met"}]
    fremd = result()
    fremd["reviewed"]["acceptance_items"] = [{"item": "A1 muss gelten", "verdict": "met"},
                                             {"item": "A2 muss gelten", "verdict": "met"},
                                             {"item": "A3 muss gelten", "verdict": "met"},
                                             {"item": "etwas voellig anderes", "verdict": "met"}]
    return [
        fall("G02.1 Reviewer bewertet KEINEN Punkt, die Task fordert drei",
             "fehlende Abdeckung schliesst PASS aus", gate(leer, ctx), (2,), "nicht bewertet"),
        fall("G02.2 Reviewer bewertet 1 von 3 Punkten",
             "A2 und A3 werden als unbewertet benannt", gate(eins, ctx), (2,), "nicht bewertet"),
        fall("G02.3 Reviewer bewertet zusaetzlich einen Punkt, den die Task nicht kennt",
             "die Zusatzbewertung bindet an nichts — PASS unerreichbar",
             gate(fremd, ctx), (2,), "ohne Entsprechung"),
    ]


def g03_testnachweis_ungeprueft() -> list[dict]:
    """Auswertung ueber ran/passed/isolated; Testrevision und Exit-Code werden nicht abgeglichen."""
    return [
        fall("G03.1 exit_code 1, aber passed:true — das Paket widerspricht sich",
             "Widerspruch schliesst PASS aus",
             gate(result(), context(tests={"ran": True, "passed": True, "isolated": True,
                                           "exit_code": 1, "ran_against": HEAD})),
             (2,), "widerspricht"),
        fall("G03.2 Tests liefen gegen eine FREMDE Revision",
             "Testnachweis gehoert nicht zur Lieferung — PASS unerreichbar",
             gate(result(), context(tests={"ran": True, "passed": True, "isolated": True,
                                           "exit_code": 0, "ran_against": FREMD})),
             (2,), "andere Revision"),
        fall("G03.3 Testlauf ohne Exit-Code und ohne Revisionsangabe",
             "ohne eigenen Nachweis ist PASS unerreichbar",
             gate(result(), context(tests={"ran": True, "passed": True, "isolated": True})),
             (2,), "ohne Exit-Code"),
    ]


def g04_modellfamilie_ungeprueft() -> list[dict]:
    """Die Trennung der Modellfamilien wird der Konfiguration ueberlassen, statt geprueft."""
    gleich = dispatch_prov(
        model="claude-opus-5",
        reviewer={"model": "claude-opus-5", "family": "anthropic", "declared_via": "probe"},
        independence={"separated": False, "why": "gleiche Familie"})
    faelle = [
        fall("G04.1 Reviewer und Builder sind dasselbe Modell",
             "kein unabhaengiges Urteil — PASS unerreichbar",
             gate(result(), context(), gleich), (2,), "dasselbe Modell"),
        fall("G04.2 gar kein Nachweis der Familientrennung vorgelegt",
             "unbewiesene Trennung ist kein Nachweis — PASS unerreichbar",
             gate(result(), context(builder={"model": None, "family": None, "declared_via": None}),
                  prov=None),
             (2,), "nicht nachgewiesen"),
    ]

    # Der Transport: sendet er ueberhaupt, wenn beide Seiten dasselbe Modell sind?
    with tempfile.TemporaryDirectory() as td:
        base, stop = mock_endpoint([json.dumps(result())])
        try:
            req = Path(td) / "REVIEW-REQUEST.md"
            req.write_text("# REVIEW REQUEST — TASK-000\n", encoding="utf-8")
            env = {**os.environ, "DEVOS_REVIEWER_API_KEY": "sk-probe",
                   "DEVOS_REVIEWER_MODEL": "claude-opus-5",
                   "DEVOS_BUILDER_MODEL": "claude-opus-5",
                   "DEVOS_REVIEWER_BASE_URL": base,
                   "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
            p = subprocess.run([sys.executable, str(DISPATCH), "--request", str(req),
                                "--out", str(Path(td) / "review_result.json")],
                               capture_output=True, text=True, env=env)
            verweigert = p.returncode == 2 and "Familie" in (p.stderr + p.stdout)
            faelle.append({"fall": "G04.3 Transport versendet an ein Modell der Builder-Familie",
                           "erwartet": "Versand wird verweigert, bevor Aufwand entsteht",
                           "defekt": not verweigert,
                           "beobachtet": f"Exit {p.returncode}"
                                         + (" — Versand verweigert" if verweigert
                                            else " — versendet bzw. anderer Grund")})
        finally:
            stop()
    return faelle


def g05_schemapruefer_ueberzeichnet() -> list[dict]:
    """Eigener Befund: jsonschema_mini fuehrt allOf/anyOf als unterstuetzt, prueft sie aber nicht."""
    sys.path.insert(0, str(HERE))
    import jsonschema_mini as J   # noqa: E402
    gefuehrt = [k for k in ("allOf", "anyOf", "oneOf", "not", "patternProperties")
                if k in getattr(J, "SUPPORTED", set())]
    nicht_durchgesetzt = []
    for k in gefuehrt:
        probe = {"type": "object", "properties": {"x": {k: [{"type": "string"}]}}}
        if not J.validate({"x": 12345}, probe):
            nicht_durchgesetzt.append(k)
    faelle = [{"fall": "G05.1 als unterstuetzt gefuehrte Konstrukte werden nicht durchgesetzt",
               "erwartet": "SUPPORTED nennt nur, was validate() wirklich prueft",
               "defekt": bool(nicht_durchgesetzt),
               "beobachtet": f"ungeprueft durchgereicht: {nicht_durchgesetzt or '—'}"}]

    offen = []
    for s_ in sorted((HERE.parent / "schema").glob("*.json")):
        offen += [f"{s_.name}: {x}"
                  for x in J.unsupported_keywords(json.loads(s_.read_text(encoding="utf-8")))]
    faelle.append({"fall": "G05.2 die eigenen Schemata benutzen nur durchgesetzte Konstrukte",
                   "erwartet": "leer — sonst steht im Schema eine Regel, die niemand prueft",
                   "defekt": bool(offen), "beobachtet": str(offen[:4]) if offen else "keine"})
    return faelle


BEFUNDE = [
    ("G01", "review_context.json ohne strukturelle Schemapruefung", g01_kontext_ohne_schemapruefung),
    ("G02", "Acceptance-Abdeckung wird nicht abgeglichen", g02_acceptance_ohne_abdeckung),
    ("G03", "Testnachweis wird nicht gegen Revision und Exit-Code geprueft", g03_testnachweis_ungeprueft),
    ("G04", "Modellfamilien-Trennung wird nicht ueberprueft", g04_modellfamilie_ungeprueft),
    ("G05", "Schemapruefer nennt mehr Konstrukte unterstuetzt als er durchsetzt", g05_schemapruefer_ueberzeichnet),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Reviewer-Befunde am laufenden Werkzeug vorfuehren")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    bericht, offen_gesamt = [], 0
    for kuerzel, titel, fn in BEFUNDE:
        faelle = fn()
        offen = [f for f in faelle if f["defekt"]]
        offen_gesamt += len(offen)
        bericht.append({"befund": kuerzel, "titel": titel,
                        "status": "VORFUEHRBAR" if offen else "BEHOBEN",
                        "faelle": faelle})

    if a.json:
        print(json.dumps({"befunde": bericht, "offene_faelle": offen_gesamt},
                         ensure_ascii=False, indent=2))
        return 1 if offen_gesamt else 0

    print("DevOS — Reproduktion der Reviewer-Befunde\n")
    for b in bericht:
        print(f"{b['befund']}  {b['titel']}")
        for f in b["faelle"]:
            mark = "OFFEN " if f["defekt"] else "behoben"
            print(f"  {mark}  {f['fall']}")
            print(f"           erwartet:  {f['erwartet']}")
            print(f"           beobachtet: {f['beobachtet']}")
        print()
    if offen_gesamt:
        print(f"{offen_gesamt} Fall/Faelle sind am aktuellen Stand vorfuehrbar — der Befund steht.")
        return 1
    print("Kein Befund mehr vorfuehrbar. Jeder Fall oben wurde gegen den Code geprueft, "
          "nicht gegen eine Beschreibung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
