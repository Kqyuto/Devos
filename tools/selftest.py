#!/usr/bin/env python3
"""Eigentest des DevOS-Werkzeugs — und er bricht ab, wenn er nicht besteht.

Das ist keine Stilfrage. Assay-Befund F006: die sechs Rechner des Workshops
erzeugen ein Feld "bestanden": false und laufen danach normal weiter. Eine Probe
ohne Abbruchwirkung ist eine Anzeige, kein Gate.

Ab Runde 2 haelt jede Probe mit dem Praefix F01..F05/N02 einen Befund fest, den
der Adversarial Reviewer gefunden hat. Sie sind Regressionsproben, keine Deko.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT, METRICS, REQUEST = HERE / "review_result.py", HERE / "delivery_metrics.py", HERE / "review_request.py"
DISPATCH = HERE / "review_dispatch.py"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED.append(name) if ok else FAILED.append((name, detail)))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  — {detail}" if detail and not ok else ""))


SENTINEL = object()


def run_gate(result: dict, context: dict | None = None, ctx_path: str | None = None,
             dispatch=SENTINEL, ctx_raw: str | None = None) -> tuple[int, dict]:
    """Ruft das Gate. `dispatch` ist standardmaessig ein nachgewiesen getrennter Lauf.

    Wer die Familientrennung pruefen will, uebergibt eine eigene Provenienz oder
    None. Alle uebrigen Proben sollen an ihrer eigenen Regel scheitern, nicht an
    einer fehlenden Provenienz.
    """
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "r.json"
        rp.write_text(json.dumps(result), encoding="utf-8")
        cmd = [sys.executable, str(RESULT), str(rp), "--json"]
        if ctx_raw is not None:
            cp = Path(td) / "c.json"
            cp.write_text(ctx_raw, encoding="utf-8")
            cmd += ["--context", str(cp)]
        elif ctx_path is not None:
            cmd += ["--context", ctx_path]
        elif context is not None:
            cp = Path(td) / "c.json"
            cp.write_text(json.dumps(context), encoding="utf-8")
            cmd += ["--context", str(cp)]
        prov = ok_dispatch() if dispatch is SENTINEL else dispatch
        if prov is not None:
            dp = Path(td) / "d.json"
            dp.write_text(json.dumps(prov), encoding="utf-8")
            cmd += ["--dispatch", str(dp)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return p.returncode, json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.returncode, {"stdout": p.stdout, "stderr": p.stderr,
                                  "gate": "GATE HAT NICHT GEURTEILT"}


HEAD_A = "4cbc9864e197aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD_B = "4cbc9864e197bbbbbbbbbbbbbbbbbbbbbbbbbbbb"   # gleiches 12-Zeichen-Praefix
BASE_A = "1111111111111111111111111111111111111111"


ACC = "der Punkt A gilt nachweislich"


def base_result(**over) -> dict:
    r = {"schema_version": "1.0", "task_id": "TASK-000",
         "reviewed_range": {"base": BASE_A, "head": HEAD_A},
         "status": "PASS", "context_sufficient": True, "blocking": [], "non_blocking": [],
         "reviewed": {"files": ["x.py"], "acceptance_items": [{"item": ACC, "verdict": "met"}],
                      "tests": {"judged": "adequate"}}}
    r.update(over)
    return r


def ok_context(**over) -> dict:
    """Ein Kontext, der das Kontextschema besteht — samt Testnachweis und Builder."""
    c = {"schema_version": "1.1", "generated_at": "2026-09-15T00:00:00+00:00",
         "generated_by": "selftest", "project": "probe",
         "task": {"id": "TASK-000", "acceptance": [ACC], "forbidden": [], "decisions": []},
         "range": {"base": BASE_A, "head": HEAD_A, "commits": []},
         "tests": {"ran": True, "passed": True, "isolated": True,
                   "exit_code": 0, "ran_against": HEAD_A},
         "builder": {"model": "claude-opus-5", "family": "anthropic", "declared_via": "selftest"},
         "files_changed": [], "diff_included": [], "referenced_ids": [], "norm_sources": {},
         "omitted": []}
    c.update(over)
    return c


def ok_dispatch(**over) -> dict:
    d = {"schema_version": "1.1", "model": "gpt-5",
         "reviewer": {"model": "gpt-5", "family": "openai", "declared_via": "Namenstabelle"},
         "builder": {"model": "claude-opus-5", "family": "anthropic", "declared_via": "selftest"},
         "independence": {"separated": True, "builder_family": "anthropic",
                          "reviewer_family": "openai", "why": "verschieden"},
         "ok": True}
    d.update(over)
    return d


def git(td: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", td, *args], capture_output=True, text=True)


GUELTIG = {
    "schema_version": "1.0", "task_id": "TASK-T",
    "reviewed_range": {"base": "b" * 40, "head": "h" * 40},
    "status": "CHANGES_REQUIRED", "context_sufficient": True,
    "blocking": [{"what": "x", "where": "a.py:1", "why": "y"}], "non_blocking": [],
    "reviewed": {"files": ["a.py"], "acceptance_items": [{"item": "A", "verdict": "met"}],
                 "tests": {"judged": "adequate"}},
}
SCHLUESSEL = "sk-geheim-darf-nirgends-auftauchen"


def mock_server(antworten: list[str], status: int = 200):
    """Lokaler OpenAI-kompatibler Endpunkt. Gibt (base_url, stop, gesehen) zurueck."""
    import http.server, threading
    gesehen = []
    folge = list(antworten)

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            gesehen.append({"auth": self.headers.get("Authorization", ""),
                            "body": json.loads(self.rfile.read(n).decode())})
            if status != 200:
                self.send_response(status); self.end_headers(); self.wfile.write(b"kaputt"); return
            inhalt = folge.pop(0) if folge else "{}"
            payload = json.dumps({"choices": [{"message": {"content": inhalt}}],
                                  "usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                                            "total_tokens": 1200}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/v1", srv.shutdown, gesehen


def dispatch(td: str, antworten: list[str], status: int = 200, env_extra: dict | None = None,
             dry: bool = False) -> tuple[int, str, str]:
    base, stop, gesehen = mock_server(antworten, status)
    try:
        req = Path(td) / "REVIEW-REQUEST.md"
        req.write_text("# REVIEW REQUEST — TASK-T\n\nPaketinhalt.\n", encoding="utf-8")
        env = {**os.environ, "DEVOS_REVIEWER_API_KEY": SCHLUESSEL,
               "DEVOS_REVIEWER_MODEL": "mock-modell", "DEVOS_REVIEWER_BASE_URL": base,
               "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
        env.update(env_extra or {})
        for k, v in list(env.items()):
            if v is None:
                env.pop(k)
        cmd = [sys.executable, str(DISPATCH), "--request", str(req),
               "--out", str(Path(td) / "review_result.json")]
        if dry:
            cmd.append("--dry-run")
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr
    finally:
        stop()


def transport_proben() -> None:
    with tempfile.TemporaryDirectory() as td:
        code, out, err = dispatch(td, [json.dumps(GUELTIG)])
        res = Path(td) / "review_result.json"
        prov = Path(td) / "review_dispatch.json"
        check("N01 gueltige Antwort => Exit 0 und review_result.json geschrieben",
              code == 0 and res.exists(), f"Exit {code} {err.strip()[:160]}")
        check("N01 Provenienz haelt Modell und Endpunkt fest",
              prov.exists() and json.loads(prov.read_text())["model"] == "mock-modell")
        geschrieben = "".join(f.read_text(encoding="utf-8") for f in Path(td).glob("*"))
        check("N01 der Schluessel taucht in KEINER geschriebenen Datei auf",
              SCHLUESSEL not in geschrieben and SCHLUESSEL not in out and SCHLUESSEL not in err)

    with tempfile.TemporaryDirectory() as td:
        code, _, _ = dispatch(td, ["kein json, nur gerede", json.dumps(GUELTIG)])
        prov = json.loads((Path(td) / "review_dispatch.json").read_text())
        check("N01 eine Reparaturrunde bei ungueltiger Antwort, dann Erfolg",
              code == 0 and len(prov["attempts"]) == 2, f"Exit {code}")
        check("N01 die verworfene Rohantwort bleibt als Beleg liegen",
              (Path(td) / "review_result_raw_r1.txt").exists())

    with tempfile.TemporaryDirectory() as td:
        code, _, _ = dispatch(td, ["murks", "wieder murks"])
        check("N01 zweimal ungueltig => Exit 4 und KEIN review_result.json",
              code == 4 and not (Path(td) / "review_result.json").exists(), f"Exit {code}")

    with tempfile.TemporaryDirectory() as td:
        bloed = dict(GUELTIG); bloed["status"] = "SIEHT_GUT_AUS"
        code, _, _ = dispatch(td, [json.dumps(bloed), json.dumps(bloed)])
        check("N01 schemawidriges Urteil wird nicht durchgereicht", code == 4, f"Exit {code}")

    with tempfile.TemporaryDirectory() as td:
        code, _, err = dispatch(td, [json.dumps(GUELTIG)], env_extra={"DEVOS_REVIEWER_API_KEY": None})
        check("N01 fehlender Schluessel => Exit 2, nicht stiller Fehlschlag",
              code == 2 and "DEVOS_REVIEWER_API_KEY" in err, f"Exit {code}")

    with tempfile.TemporaryDirectory() as td:
        code, _, err = dispatch(td, [], status=500)
        check("N01 Endpunktfehler => Exit 5 mit Klartext", code == 5 and "HTTP 500" in err,
              f"Exit {code} {err.strip()[:120]}")

    with tempfile.TemporaryDirectory() as td:
        code, out, _ = dispatch(td, [json.dumps(GUELTIG)], dry=True)
        check("N01 dry-run schreibt die Anfrage und sendet nichts",
              code == 0 and (Path(td) / "review_request_payload.json").exists()
              and not (Path(td) / "review_dispatch.json").exists(), f"Exit {code}")


def mock_endpoint(antworten: list[str]):
    """Wie mock_server, gibt aber zusaetzlich die gesehenen Anfragen zurueck."""
    return mock_server(antworten)


def e2e_proben() -> None:
    """Die ganze Kette an einem echten Repo: Paket, Nachforderung, Urteil, Gate.

    Die Einzelproben oben pruefen jede Regel fuer sich. Diese Probe prueft, dass
    die Teile ueberhaupt zusammenpassen — ein Kontext, den der Erzeuger schreibt
    und das eigene Gate nicht annimmt, faellt sonst niemandem auf.
    """
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "quelle.md").write_text("Der committete Wortlaut.\n", encoding="utf-8")
        (Path(td) / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (Path(td) / "test_a.py").write_text("from a import f\nassert f() == 1\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        (Path(td) / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        (Path(td) / "test_a.py").write_text("from a import f\nassert f() == 2\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        head = git(td, "rev-parse", "HEAD").stdout.strip()
        base = git(td, "rev-parse", "HEAD~1").stdout.strip()

        # Nur im Arbeitsverzeichnis: darf den Reviewer NIE erreichen.
        (Path(td) / "quelle.md").write_text("MANIPULIERT — nur lokal.\n", encoding="utf-8")
        (Path(td) / "nur_lokal.md").write_text("existiert im geprueften Stand nicht\n", encoding="utf-8")

        acc = ["f() liefert 2", "ein Test deckt den neuen Rueckgabewert ab"]
        tk = Path(td) / "TASK-E2E.md"
        tk.write_text("# TASK-E2E — Rueckgabewert\n\n## Acceptance\n"
                      + "".join(f"- {x}\n" for x in acc)
                      + "\n## Forbidden\n- nichts\n\n## Offen / Unentschieden\n- ob spaeter 3\n",
                      encoding="utf-8")
        out = Path(td) / "work" / "review"
        env = {**os.environ, "DEVOS_BUILDER_MODEL": "claude-opus-5"}
        p = subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                            "--base", base, "--head", head, "--out", "work/review",
                            "--tests", "python3 test_a.py"],
                           capture_output=True, text=True, env=env)
        check("E2E Paket erzeugt", p.returncode == 0, p.stderr.strip()[:200])
        ctx = json.loads((out / "review_context.json").read_text(encoding="utf-8"))

        sys.path.insert(0, str(HERE))
        import jsonschema_mini as J   # noqa: E402
        fehler = J.validate(ctx, json.loads(
            (HERE.parent / "schema" / "review_context.schema.json").read_text(encoding="utf-8")))
        check("E2E der erzeugte Kontext besteht das eigene Kontextschema", not fehler, str(fehler[:3]))
        check("E2E der Kontext nennt Builder-Modell und -Familie",
              ctx["builder"]["family"] == "anthropic", json.dumps(ctx.get("builder")))
        check("E2E der Testnachweis ist an den geprueften Head gebunden",
              ctx["tests"]["ran_against"] == head and ctx["tests"]["exit_code"] == 0,
              json.dumps(ctx["tests"])[:160])
        check("E2E der Bestand des geprueften Stands liegt bei",
              ctx["inventory"]["count"] == 3 and "quelle.md" in ctx["inventory"]["paths"],
              json.dumps(ctx["inventory"])[:160])
        check("E2E nur lokal vorhandene Dateien stehen NICHT im Bestand",
              "nur_lokal.md" not in ctx["inventory"]["paths"])

        def antwort(status, items, missing=None):
            o = {"schema_version": "1.0", "task_id": "TASK-E2E",
                 "reviewed_range": {"base": base, "head": head}, "status": status,
                 "context_sufficient": status != "INSUFFICIENT_CONTEXT",
                 "blocking": [], "non_blocking": [],
                 "reviewed": {"files": ["a.py"], "acceptance_items": items,
                              "tests": {"judged": "adequate"}}}
            if missing:
                o["missing_context"] = missing
            return json.dumps(o)

        nach = antwort("INSUFFICIENT_CONTEXT", [], ["quelle.md", "nur_lokal.md"])
        gut = antwort("PASS", [{"item": x, "verdict": "met"} for x in acc])
        base_url, stop, gesehen = mock_endpoint([nach, gut])
        try:
            env2 = {**env, "DEVOS_REVIEWER_API_KEY": SCHLUESSEL, "DEVOS_REVIEWER_MODEL": "gpt-5",
                    "DEVOS_REVIEWER_BASE_URL": base_url,
                    "DEVOS_REVIEWER_PRICE_IN": "1.25", "DEVOS_REVIEWER_PRICE_OUT": "10",
                    "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
            p = subprocess.run([sys.executable, str(DISPATCH), "--request", str(out / "REVIEW-REQUEST.md"),
                                "--out", str(out / "review_result.json"),
                                "--context", str(out / "review_context.json"), "--root", td],
                               capture_output=True, text=True, env=env2)
        finally:
            stop()
        check("E2E Transport holt ein Urteil", p.returncode == 0, p.stderr.strip()[:200])
        prov = json.loads((out / "review_dispatch.json").read_text(encoding="utf-8"))
        gesendet = json.dumps(gesehen, ensure_ascii=False)
        check("E2E der Reviewer bekommt eine nachgeforderte Quelle nachgereicht",
              "Der committete Wortlaut." in gesendet)
        check("E2E die Nachforderung liest aus der REVISION, nicht aus dem Arbeitsverzeichnis",
              "MANIPULIERT" not in gesendet, "lokaler Stand ist beim Reviewer gelandet")
        check("E2E eine nur lokal vorhandene Datei wird verweigert und der Grund genannt",
              any(q.get("asked") == "nur_lokal.md" and q.get("refused_why")
                  for q in prov["source_requests"]), json.dumps(prov["source_requests"])[:200])
        check("E2E genau EINE Nachforderungsrunde, dann zaehlt das Urteil",
              prov["calls"] == 2, f"calls={prov['calls']}")
        check("E2E Token und Kosten werden festgehalten",
              prov["cost_usd"] is not None and prov["usage_total"]["prompt_tokens"],
              json.dumps(prov.get("usage_total")))

        p = subprocess.run([sys.executable, str(RESULT), str(out / "review_result.json"),
                            "--context", str(out / "review_context.json"),
                            "--dispatch", str(out / "review_dispatch.json"), "--json"],
                           capture_output=True, text=True)
        # Abdeckungs-Reparatur: ein Reviewer, der Punkte uebergeht, wird EINMAL
        # gefragt — nicht durchgewunken und nicht endlos.
        halb = antwort("PASS", [{"item": acc[0], "verdict": "met"}])
        voll = antwort("PASS", [{"item": x, "verdict": "met"} for x in acc])
        base_url, stop, gesehen2 = mock_endpoint([halb, voll])
        env3 = {**env2, "DEVOS_REVIEWER_BASE_URL": base_url}
        try:
            p2 = subprocess.run([sys.executable, str(DISPATCH),
                                 "--request", str(out / "REVIEW-REQUEST.md"),
                                 "--out", str(out / "r_cov.json"),
                                 "--context", str(out / "review_context.json"), "--root", td],
                                capture_output=True, text=True, env=env3)
        finally:
            stop()
        prov2 = json.loads((out / "review_dispatch.json").read_text(encoding="utf-8"))
        erg = json.loads((out / "r_cov.json").read_text(encoding="utf-8"))
        check("E2E ein Reviewer, der Acceptance-Punkte uebergeht, wird EINMAL nachgefragt",
              p2.returncode == 0 and prov2.get("coverage_repair") is True
              and len(erg["reviewed"]["acceptance_items"]) == len(acc),
              f"Exit {p2.returncode} repair={prov2.get('coverage_repair')}")
        check("E2E die Nachfrage nennt die fehlenden Punkte im Wortlaut",
              acc[1] in json.dumps(gesehen2, ensure_ascii=False)
              and "AENDERE DEIN URTEIL NICHT" in json.dumps(gesehen2, ensure_ascii=False))
        halb2 = antwort("PASS", [{"item": acc[0], "verdict": "met"}])
        base_url, stop, _ = mock_endpoint([halb2, halb2, halb2, halb2])
        env4 = {**env2, "DEVOS_REVIEWER_BASE_URL": base_url}
        try:
            p3 = subprocess.run([sys.executable, str(DISPATCH),
                                 "--request", str(out / "REVIEW-REQUEST.md"),
                                 "--out", str(out / "r_cov2.json"),
                                 "--context", str(out / "review_context.json"), "--root", td],
                                capture_output=True, text=True, env=env4)
        finally:
            stop()
        prov3 = json.loads((out / "review_dispatch.json").read_text(encoding="utf-8"))
        check("E2E bleibt er unvollstaendig, wird das Urteil durchgereicht — das Gate "
              "entscheidet, nicht der Transport",
              p3.returncode == 0 and prov3["calls"] == 2, f"Exit {p3.returncode} calls={prov3['calls']}")
        p4 = subprocess.run([sys.executable, str(RESULT), str(out / "r_cov2.json"),
                             "--context", str(out / "review_context.json"),
                             "--dispatch", str(out / "review_dispatch.json"), "--json"],
                            capture_output=True, text=True)
        check("E2E und das Gate laesst ein unvollstaendiges Urteil nicht als PASS durch",
              p4.returncode == 2, f"Exit {p4.returncode}")

        check("E2E das Gate nimmt die echte Kette an und gibt sie dem Menschen frei",
              p.returncode == 0, f"Exit {p.returncode} {p.stdout.strip()[:200]}")

        # Und der umgekehrte Beweis: gleiche Familie => gar kein Versand.
        base_url, stop, _ = mock_endpoint([gut])
        try:
            env3 = {**env2, "DEVOS_REVIEWER_MODEL": "claude-sonnet-5", "DEVOS_REVIEWER_BASE_URL": base_url}
            p = subprocess.run([sys.executable, str(DISPATCH), "--request", str(out / "REVIEW-REQUEST.md"),
                                "--out", str(out / "r2.json"),
                                "--context", str(out / "review_context.json"), "--root", td],
                               capture_output=True, text=True, env=env3)
        finally:
            stop()
        check("E2E Reviewer aus der Builder-Familie => Versand verweigert, bevor Aufwand entsteht",
              p.returncode == 2 and "Familie" in p.stderr and not (out / "r2.json").exists(),
              f"Exit {p.returncode} {p.stderr.strip()[:160]}")



# ---------------------------------------------------------------- Orchestrator

RUN_TASK = HERE / "run_task.py"
HANDOVER = HERE / "handover.py"


def reviewer_server(urteil):
    """Ein Reviewer, der den geprueften Head aus dem Paket liest.

    Muss er auch: das Gate vergleicht `reviewed_range.head` zeichengenau, und
    der Head aendert sich mit jeder Runde. Ein fester SHA wuerde ab Runde 2
    INSUFFICIENT_CONTEXT erzeugen — und damit etwas anderes pruefen als gemeint.
    """
    import http.server, re, threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode())
            paket = body["messages"][-1]["content"]
            m = re.search(r"\*\*head\*\* `([0-9a-f]{40})`", paket)
            b = re.search(r"\*\*base\*\* `([0-9a-f]{40})`", paket)
            inhalt = urteil(b.group(1) if b else "?", m.group(1) if m else "?")
            pay = json.dumps({"choices": [{"message": {"content": inhalt}}],
                              "usage": {"prompt_tokens": 900, "completion_tokens": 150}}).encode()
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


ORCH_ACC = "f() liefert 2"


def orch_urteil(status="PASS", blocking=None, items=None):
    def f(base, head):
        return json.dumps({
            "schema_version": "1.0", "task_id": "TASK-O",
            "reviewed_range": {"base": base, "head": head}, "status": status,
            "context_sufficient": True, "blocking": blocking or [], "non_blocking": [],
            "reviewed": {"files": ["a.py"],
                         "acceptance_items": items if items is not None
                         else [{"item": ORCH_ACC, "verdict": "met"}],
                         "tests": {"judged": "adequate"}}})
    return f


def orch_repo(builder_skript: str, limits: dict | None = None) -> tuple[str, str]:
    """Ein Repo mit Task, rotem Test und einem Builder-Skript. Gibt (root, base)."""
    td = tempfile.mkdtemp(prefix="devos-orch-")
    git(td, "init", "-q", ".")
    git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
    (Path(td) / ".devos.json").write_text(json.dumps(
        {"project": "probe", "limits": limits or {"max_correction_rounds": 2}}), encoding="utf-8")
    (Path(td) / "a.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    (Path(td) / "test_a.py").write_text(
        "from a import f\nassert f() == 2, 'f() liefert nicht 2'\n", encoding="utf-8")
    (Path(td) / "TASK-O.md").write_text(
        f"# TASK-O — f liefert 2\n\n## Acceptance\n- {ORCH_ACC}\n\n"
        "## Forbidden\n- nichts\n\n## Offen / Unentschieden\n- nichts\n", encoding="utf-8")
    (Path(td) / "builder.sh").write_text(builder_skript, encoding="utf-8")
    git(td, "add", "."); git(td, "commit", "-qm", "basis")
    return td, git(td, "rev-parse", "HEAD").stdout.strip()


def orch_run(td: str, base: str, urteil, extra: list[str] | None = None,
             env_extra: dict | None = None, builder: str | None = "bash builder.sh"):
    burl, stop = reviewer_server(urteil)
    try:
        env = {**os.environ, "DEVOS_BUILDER_MODEL": "claude-opus-5",
               "DEVOS_REVIEWER_API_KEY": SCHLUESSEL, "DEVOS_REVIEWER_MODEL": "gpt-5",
               "DEVOS_REVIEWER_BASE_URL": burl,
               "DEVOS_REVIEWER_PRICE_IN": "1.25", "DEVOS_REVIEWER_PRICE_OUT": "10",
               "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
        if builder:
            env["DEVOS_BUILDER_CMD"] = builder
        env.update(env_extra or {})
        for k, v in list(env.items()):
            if v is None:
                env.pop(k)
        cmd = [sys.executable, str(RUN_TASK), "--task", str(Path(td) / "TASK-O.md"),
               "--root", td, "--base", base, "--tests", "python3 test_a.py"] + (extra or [])
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    finally:
        stop()
    sf = Path(td) / "work" / "runs" / "TASK-O" / "run_state.json"
    st = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
    return p, st


BUILDER_ZWEI_RUNDEN = """#!/bin/bash
set -e
if [ "$DEVOS_ROUND" = "1" ]; then printf 'def f():\\n    return 1\\n' > a.py
else printf 'def f():\\n    return 2\\n' > a.py; fi
git add -A && git commit -qm "builder runde $DEVOS_ROUND"
"""

# Liefert nie 2 — die Probe soll das Budget treffen, nicht zufaellig gruen werden.
BUILDER_IMMER_FALSCH = """#!/bin/bash
set -e
printf 'def f():\\n    return 9%s\\n' "$DEVOS_ROUND" > a.py
git add -A && git commit -qm "builder runde $DEVOS_ROUND"
"""


def orchestrator_proben() -> None:
    # --- der gesunde Durchlauf: roter Test, Korrektur, Urteil, Human Gate
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN)
    p, st = orch_run(td, base, orch_urteil())
    check("O1 Durchlauf endet beim Menschen mit PASS, Exit 0",
          p.returncode == 0 and st.get("outcome") == "PASS", f"Exit {p.returncode} {p.stderr[:200]}")
    check("O1 fehlgeschlagene Maschinentests rufen den Reviewer NICHT",
          st["spent"]["reviewer_calls"] == 1 and st["spent"]["builder_calls"] == 2,
          json.dumps(st.get("spent")))
    check("O1 eine Korrektur nach rotem Test zaehlt als Korrekturrunde",
          st["spent"]["corrections"] == 1, str(st["spent"].get("corrections")))
    r1 = next(r for r in st["rounds"] if r["n"] == 1)
    check("O1 Runde 1 hat gebaut und gepackt, aber nie geurteilt",
          r1["package"]["status"] == "ok" and r1["review"] is None)
    check("O1 der Brief der Korrekturrunde nennt den Fehlschlag",
          "Maschinentests sind fehlgeschlagen"
          in (Path(td) / "work/runs/TASK-O/round-2/BUILD-BRIEF.md").read_text(encoding="utf-8"))
    check("O1 PASS uebernimmt nichts — der Branch bleibt, wo er ist",
          git(td, "rev-parse", "HEAD").stdout.strip() == st["rounds"][-1]["head"])
    hv = Path(td) / "work/runs/TASK-O/HANDOVER.md"
    check("O1 das Entscheidungsblatt wird erzeugt und nennt den geprueften Commit",
          hv.exists() and st["rounds"][-1]["head"] in hv.read_text(encoding="utf-8"))
    check("O1 Kosten werden ueber den Lauf summiert", st["spent"]["cost_usd"] is not None,
          str(st["spent"].get("cost_usd")))

    # --- das Budget haelt: zwei Korrekturrunden, dann entscheidet der Mensch
    td, base = orch_repo(BUILDER_IMMER_FALSCH)
    p, st = orch_run(td, base, orch_urteil())
    check("O2 dauerhaft rote Tests => nach 2 Korrekturrunden Human Gate, Exit 1",
          p.returncode == 1 and st["spent"]["corrections"] == 2, f"Exit {p.returncode}")
    check("O2 keine unbegrenzten Wiederholungen: hoechstens 3 Builder-Aufrufe",
          st["spent"]["builder_calls"] == 3, str(st["spent"]["builder_calls"]))
    check("O2 der Grund steht im Zustand, nicht nur auf dem Bildschirm",
          "Korrekturrunden verbraucht" in (st.get("stop_reason") or ""), st.get("stop_reason"))

    # --- CHANGES_REQUIRED zaehlt genauso
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN)
    blocker = [{"what": "x", "where": "a.py:1", "why": "y"}]
    p, st = orch_run(td, base, orch_urteil("CHANGES_REQUIRED", blocker))
    check("O3 CHANGES_REQUIRED loest Korrekturrunden aus und endet im Budget",
          p.returncode == 1 and st["spent"]["corrections"] == 2
          and st["spent"]["reviewer_calls"] == 2, json.dumps(st.get("spent")))
    check("O3 der Brief traegt den Befund des Reviewers zum Builder zurueck",
          "a.py:1" in (Path(td) / "work/runs/TASK-O/round-3/BUILD-BRIEF.md").read_text(encoding="utf-8"))

    # --- CONFLICT geht sofort zum Menschen, ohne weitere Runde
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN)

    def mit_konflikt(b, h):
        o = json.loads(orch_urteil("CHANGES_REQUIRED", [{"what": "x", "where": "a.py:1",
                                                         "why": "y"}])(b, h))
        o["governance_conflicts"] = [{"id": "D-144", "what": "Rechte erweitert"}]
        return json.dumps(o)
    p, st = orch_run(td, base, mit_konflikt)
    check("O4 CONFLICT fuehrt sofort zum Menschen, ohne weitere Korrekturrunde",
          p.returncode == 2 and st["spent"]["corrections"] == 1
          and "CONFLICT" in (st.get("stop_reason") or ""), f"Exit {p.returncode} {st.get('stop_reason')}")

    # --- Fortsetzung nach Absturz: der teure Schritt wird nicht wiederholt
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN + "\necho $DEVOS_ROUND >> " + f"{td}/aufrufe.log\n")
    p, st = orch_run(td, base, orch_urteil(), extra=["--max-rounds", "0"])
    check("O5 Vorbereitung: erster Lauf endet nach Runde 1 im Budget", p.returncode == 1,
          f"Exit {p.returncode}")
    vorher = (Path(td) / "aufrufe.log").read_text(encoding="utf-8").count("\n") \
        if (Path(td) / "aufrufe.log").exists() else 0
    kopf1 = git(td, "rev-parse", "HEAD").stdout.strip()
    p2, st2 = orch_run(td, base, orch_urteil(), extra=["--max-rounds", "0"])
    nachher = (Path(td) / "aufrufe.log").read_text(encoding="utf-8").count("\n") \
        if (Path(td) / "aufrufe.log").exists() else 0
    check("O5 Fortsetzung ruft den Builder fuer eine gebaute Runde NICHT erneut",
          nachher == vorher and git(td, "rev-parse", "HEAD").stdout.strip() == kopf1,
          f"Aufrufe {vorher} -> {nachher}")

    # --- eine veraenderte Task macht den Lauf ungueltig
    (Path(td) / "TASK-O.md").write_text("# TASK-O — etwas ganz anderes\n\n## Acceptance\n- neu\n",
                                        encoding="utf-8")
    p3, _ = orch_run(td, base, orch_urteil())
    check("O6 veraenderte Taskdatei => Fortsetzung verweigert, kein Urteil an einem Phantom",
          p3.returncode == 5 and "geaendert" in p3.stderr, f"Exit {p3.returncode} {p3.stderr[:160]}")

    # --- der Builder liefert keinen Commit
    td, base = orch_repo("#!/bin/bash\necho 'ich baue nichts'\n")
    p, st = orch_run(td, base, orch_urteil())
    check("O7 Builder ohne Commit => technische Blockade, Exit 5",
          p.returncode == 5 and "keinen neuen Commit" in (st.get("stop_reason") or ""),
          f"Exit {p.returncode} {st.get('stop_reason')}")

    # --- der Builder laesst das Arbeitsverzeichnis schmutzig
    td, base = orch_repo("#!/bin/bash\nset -e\nprintf 'def f():\\n    return 2\\n' > a.py\n"
                         "git add -A && git commit -qm c\necho 'rest' > uebrig.txt\n")
    p, st = orch_run(td, base, orch_urteil())
    check("O8 schmutziges Arbeitsverzeichnis nach dem Bau => Blockade statt ungebundener Lieferung",
          p.returncode == 5 and "nicht sauber" in (st.get("stop_reason") or ""),
          f"Exit {p.returncode} {st.get('stop_reason')}")

    # --- ohne Builder: der Auftrag wird geschrieben, der Mensch liefert
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN)
    p, st = orch_run(td, base, orch_urteil(), extra=["--no-builder"], builder=None)
    brief = Path(td) / "work/runs/TASK-O/round-1/BUILD-BRIEF.md"
    check("O9 ohne Builder wird der Auftrag geschrieben und an den Menschen uebergeben",
          p.returncode == 2 and brief.exists() and "BUILD-BRIEF" in brief.read_text(encoding="utf-8"),
          f"Exit {p.returncode}")
    (Path(td) / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    git(td, "add", "."); git(td, "commit", "-qm", "von Hand")
    p, st = orch_run(td, base, orch_urteil(), extra=["--no-builder", "--restart"], builder=None)
    check("O9 nach dem Commit von Hand laeuft dieselbe Task bis zum Urteil durch",
          p.returncode == 0 and st.get("outcome") == "PASS", f"Exit {p.returncode} {p.stderr[:200]}")

    # --- der Reviewer bewertet nichts: das Gate faengt es, der Orchestrator traegt es weiter
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN)
    p, st = orch_run(td, base, orch_urteil(items=[]))
    check("O10 ein Reviewer, der nichts bewertet, erzeugt kein PASS im Gesamtlauf",
          p.returncode == 2 and st.get("outcome") != "PASS", f"Exit {p.returncode}")


CHECKREG = HERE / "check_registers.py"


def registerproben() -> None:
    """Der Pruefer darf nicht melden, was das Projekt absichtlich so baut.

    Jede dieser Proben haelt einen Fehlalarm fest, den die erste Fassung an einem
    ECHTEN Bestand erzeugt hat. Ein Pruefer mit Fehlalarmen ist schlimmer als
    keiner: man gewoehnt sich an Rot.
    """
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "registers").mkdir()
        (Path(td) / "tools").mkdir()
        (Path(td) / ".devos.json").write_text(json.dumps({
            "project": "p", "id_pattern": r"\b(OQ-\d{3}|R-\d{3})\b",
            "registers": {
                "OQ": {"path": "registers/oq.md", "kind": "row", "multi_row": True},
                "R": {"path": "registers/r.md", "kind": "row"}},
            "ignore_paths": ["tools/*.py"],
            "tests": "echo hallo"}), encoding="utf-8")
        # OQ wird fortgeschrieben: drei Zeilen zu OQ-001, die letzte gilt.
        (Path(td) / "registers" / "oq.md").write_text(
            "| ID | Frage | Status |\n|---|---|---|\n"
            "| OQ-001 | erste Fassung | offen |\n"
            "| OQ-001 | zweite Fassung | verengt |\n"
            "| OQ-001 | dritte Fassung | geschlossen |\n"
            "| OQ-002 | mit Vermerk | offen | **Nachtrag:** dazugeschrieben |\n"
            "| OQ-003 | abgeschnitten |\n", encoding="utf-8")
        (Path(td) / "registers" / "r.md").write_text(
            "| ID | Risiko |\n|---|---|\n| R-001 | etwas |\n", encoding="utf-8")
        # Ein Testfixture im Werkzeugverzeichnis — keine Referenz.
        (Path(td) / "tools" / "rechner.py").write_text(
            'PROBE = "| OQ-999 | Gegenstand | offen |"\n', encoding="utf-8")
        (Path(td) / "doku.md").write_text(
            "OQ-001 und R-001 werden hier genannt. Die Lint-Regel R-7 nicht.\n",
            encoding="utf-8")

        p = subprocess.run([sys.executable, str(CHECKREG), "--root", td, "--json"],
                           capture_output=True, text=True)
        js = json.loads(p.stdout)
        befunde = " | ".join(js["befunde"])
        check("R1 ein fortgeschriebenes Register ist keine Doppeldefinition",
              "Doppeldefinition" not in befunde and "OQ-001" in js["mehrfassungen"],
              befunde[:180])
        check("R2 eine ID im ausgenommenen Werkzeugverzeichnis ist keine Referenz",
              "OQ-999" not in befunde, befunde[:180])
        check("R3 eine angehaengte Vermerkspalte ist ein Hinweis, kein Befund",
              not any("OQ-002" in b for b in js["befunde"])
              and any("OQ-002" in h for h in js["formhinweise"]),
              json.dumps(js["formhinweise"])[:180])
        check("R4 eine ABGESCHNITTENE Zeile ist sehr wohl ein Befund — da fehlt Inhalt",
              any("OQ-003" in b and "fehlt Inhalt" in b for b in js["befunde"]),
              befunde[:180])
        check("R5 ein enges ID-Muster schuetzt vor fremden ID-Raeumen (R-7 ist Lint)",
              "R-7" not in befunde, befunde[:180])

        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        p = subprocess.run([sys.executable, str(PREFLIGHT), "--root", td, "--json"],
                           capture_output=True, text=True)
        punkte = {z["punkt"]: z for z in json.loads(p.stdout)["checks"]}
        check("R6 das Testkommando aus .devos.json wird uebernommen — niemand tippt es zweimal",
              punkte.get("Testkommando", {}).get("zustand") == "ok"
              and "aus .devos.json" in punkte["Testkommando"]["detail"],
              json.dumps(punkte.get("Testkommando"))[:160])


GRAPHIFY = HERE / "graphify_adapter.py"
PREFLIGHT = HERE / "preflight.py"


def mcp_server(antwort_fn, sse: bool = False, status: int = 200):
    """Ein MCP-Server, so echt wie noetig: initialize, tools/list, tools/call."""
    import http.server, threading
    gesehen: list[dict] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            nachricht = json.loads(self.rfile.read(n).decode())
            gesehen.append({"msg": nachricht,
                            "auth": self.headers.get("Authorization", ""),
                            "session": self.headers.get("Mcp-Session-Id")})
            if status != 200:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"nope"}')
                return
            methode = nachricht.get("method")
            if methode == "notifications/initialized":
                self.send_response(202); self.end_headers(); return
            if methode == "initialize":
                ergebnis = {"protocolVersion": nachricht["params"]["protocolVersion"],
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "graphify-attrappe", "version": "0.1"}}
            elif methode == "tools/list":
                ergebnis = {"tools": antwort_fn("tools")}
            elif methode == "tools/call":
                ergebnis = antwort_fn("call", nachricht["params"])
            else:
                ergebnis = {}
            koerper = json.dumps({"jsonrpc": "2.0", "id": nachricht.get("id"),
                                  "result": ergebnis})
            if sse:
                nutz = f"event: message\ndata: {koerper}\n\n".encode()
                ctype = "text/event-stream"
            else:
                nutz = koerper.encode()
                ctype = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(nutz)))
            if methode == "initialize":
                self.send_header("Mcp-Session-Id", "sitzung-4711")
            self.end_headers()
            self.wfile.write(nutz)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/mcp", srv.shutdown, gesehen


SUCHWERKZEUG = [{"name": "search_context",
                 "description": "Durchsucht den privaten Index nach Kontext.",
                 "inputSchema": {"type": "object", "required": ["query"],
                                 "properties": {"query": {"type": "string"},
                                                "limit": {"type": "integer"}}}}]


def graphify_lauf(td: str, antwort_fn, sse: bool = False, status: int = 200,
                  ids: str = "XY-007", env_extra: dict | None = None):
    url, stop, gesehen = mcp_server(antwort_fn, sse, status)
    try:
        env = {**os.environ, "DEVOS_GRAPHIFY_URL": url,
               "DEVOS_GRAPHIFY_KEY": SCHLUESSEL,
               "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
        env.update(env_extra or {})
        p = subprocess.run([sys.executable, str(GRAPHIFY), "query", "--root", td,
                            "--rev", "HEAD", "--ids", ids],
                           capture_output=True, text=True, env=env)
    finally:
        stop()
    try:
        return json.loads(p.stdout), p, gesehen
    except json.JSONDecodeError:
        return {}, p, gesehen


def graphify_proben() -> None:
    sys.path.insert(0, str(HERE))
    import jsonschema_mini as J   # noqa: E402
    td, head = index_repo()          # reg.md enthaelt "## XY-007 — Erste Norm" + "Wortlaut."

    def mit(inhalt):
        def fn(was, params=None):
            if was == "tools":
                return SUCHWERKZEUG
            return inhalt
        return fn

    # --- Y1/Y2: ein echter Vorschlag wird mit EXAKTER Zeile belegt
    treffer = {"structuredContent": {"results": [
        {"path": "reg.md", "text": "Wortlaut.", "score": 0.9}]}}
    erg, p, gesehen = graphify_lauf(td, mit(treffer))
    h = erg.get("hits") or []
    check("Y1 der Adapter verbindet, entdeckt Werkzeuge und waehlt eines",
          erg.get("source", "").startswith("graphify:search_context"),
          f"{erg.get('source')} · {erg.get('why')}")
    check("Y2 ein Vorschlag wird im geprueften Stand wiedergefunden — mit exakter Zeile",
          len(h) == 1 and h[0]["path"] == "reg.md" and h[0]["line"] == 3
          and h[0]["rev"] == head, json.dumps(h)[:200])
    check("Y2 jeder Treffer erfuellt den DevOS-Vertrag (id, path, line, rev)",
          all(all(t.get(k) not in (None, "") for k in ("id", "path", "line", "rev")) for t in h))
    check("Y1 der Sitzungskopf des Servers wird mitgefuehrt",
          any(g["session"] == "sitzung-4711" for g in gesehen),
          str([g["session"] for g in gesehen]))
    check("Y1 der Schluessel taucht in der Ausgabe NICHT auf",
          SCHLUESSEL not in p.stdout and SCHLUESSEL not in p.stderr)

    # --- Y3: ein Pfad, den es im geprueften Stand nicht gibt
    erg, _, _ = graphify_lauf(td, mit({"structuredContent": {"results": [
        {"path": "gibt/es/nicht.md", "text": "irgendwas"}]}}))
    check("Y3 ein veralteter Index kann nichts durchschmuggeln: unbekannter Pfad wird verworfen",
          not erg["hits"] and erg["rejected"]
          and "existiert in" in erg["rejected"][0]["why"], json.dumps(erg)[:220])

    # --- Y4: echter Pfad, aber Inhalt aus einer aelteren Fassung
    erg, _, _ = graphify_lauf(td, mit({"structuredContent": {"results": [
        {"path": "reg.md", "text": "Diesen Satz gab es hier nie und XY-999 auch nicht"}]}}),
        ids="XY-999")
    check("Y4 echter Pfad, aber Schnipsel nicht im geprueften Stand => verworfen",
          not erg["hits"] and erg["rejected"], json.dumps(erg)[:220])

    # --- Y5: der Dienst liefert nur Prosa
    erg, _, _ = graphify_lauf(td, mit({"content": [
        {"type": "text", "text": "XY-007 regelt die Normfrage. Mehr kann ich nicht sagen."}]}))
    check("Y5 nur Prosa ohne Fundstelle => keine Treffer, kein Absturz",
          erg["hits"] == [] and erg["coverage"]["verified"] == 0, json.dumps(erg)[:200])

    # --- Y6: Pfade im Fliesstext werden erkannt und verifiziert
    erg, _, _ = graphify_lauf(td, mit({"content": [
        {"type": "text", "text": "Siehe `reg.md` — dort steht der Wortlaut zu XY-007."}]}))
    check("Y6 ein Pfad im Fliesstext wird erkannt und am Original verifiziert",
          len(erg["hits"]) == 1 and erg["hits"][0]["path"] == "reg.md",
          json.dumps(erg["hits"])[:200])

    # --- Y7: SSE-Antwort
    erg, _, _ = graphify_lauf(td, mit(treffer), sse=True)
    check("Y7 eine Antwort als Ereignisstrom (SSE) wird verstanden",
          len(erg.get("hits") or []) == 1, json.dumps(erg)[:200])

    # --- Y8: Server antwortet 401
    erg, p, _ = graphify_lauf(td, mit(treffer), status=401)
    check("Y8 ein abgelehnter Schluessel ergibt eine klare Meldung, keinen Absturz",
          erg["hits"] == [] and "Schluessel" in erg["why"], json.dumps(erg)[:200])
    check("Y8 und auch dann steht der Schluessel nirgends in der Ausgabe",
          SCHLUESSEL not in p.stdout and SCHLUESSEL not in p.stderr)

    # --- Y9: Werkzeug mit unfuellbarem Pflichtfeld
    seltsam = [{"name": "traverse",
                "inputSchema": {"type": "object", "required": ["node_id", "query"],
                                "properties": {"node_id": {"type": "string"},
                                               "query": {"type": "string"}}}}]
    erg, _, _ = graphify_lauf(td, lambda was, params=None: seltsam if was == "tools" else {})
    check("Y9 ein Pflichtfeld, das der Adapter nicht fuellen kann, wird BENANNT statt geraten",
          not erg["hits"] and "node_id" in erg["why"], erg.get("why", "")[:160])
    erg, _, _ = graphify_lauf(td, lambda was, params=None: seltsam if was == "tools" else treffer,
                              env_extra={"DEVOS_GRAPHIFY_ARGS": '{"node_id": "XY-007"}'})
    check("Y9 mit DEVOS_GRAPHIFY_ARGS laesst es sich ergaenzen",
          len(erg.get("hits") or []) == 1, json.dumps(erg)[:200])

    # --- Y10: gezielte Werkzeugwahl
    zwei = SUCHWERKZEUG + [{"name": "ingest",
                            "inputSchema": {"type": "object", "properties": {}}}]
    erg, _, _ = graphify_lauf(td, lambda was, params=None: zwei if was == "tools" else treffer,
                              env_extra={"DEVOS_GRAPHIFY_TOOL": "gibtsnicht"})
    check("Y10 ein nicht vorhandenes Werkzeug wird benannt, nicht stillschweigend ersetzt",
          not erg["hits"] and "gibt es auf diesem Server nicht" in erg["why"],
          erg.get("why", "")[:140])

    # --- Y11: der Adapter erfuellt den DevOS-Vertrag im echten Paket
    url, stop, _ = mcp_server(mit(treffer))
    try:
        tk = Path(td) / "T.md"
        tk.write_text("# T-G — Probe\n\n## Acceptance\n- XY-007 gilt\n", encoding="utf-8")
        env = {**os.environ, "DEVOS_BUILDER_MODEL": "claude-opus-5",
               "DEVOS_GRAPH_CMD": f"{sys.executable} {GRAPHIFY}",
               "DEVOS_GRAPHIFY_URL": url, "DEVOS_GRAPHIFY_KEY": SCHLUESSEL,
               "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
        subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                        "--base", "HEAD~1", "--head", "HEAD", "--out", "revg"],
                       capture_output=True, text=True, env=env)
    finally:
        stop()
    c = json.loads((Path(td) / "revg" / "review_context.json").read_text(encoding="utf-8"))
    g = c["graph"]
    check("Y11 im echten Paket wird Graphify als Quelle gefuehrt und benutzt",
          g["used"] is True and str(g["source"]).startswith("graphify"),
          json.dumps({k: g[k] for k in ("used", "source", "why")})[:220])
    fehler = J.validate(c, json.loads(
        (HERE.parent / "schema" / "review_context.schema.json").read_text(encoding="utf-8")))
    check("Y11 der Kontext mit Graphify-Treffern besteht das Kontextschema",
          not fehler, str(fehler[:2]))
INDEX = HERE / "context_index.py"


def preflight_proben() -> None:
    """Der Bereitschaftstest muss den fehlenden Posten NENNEN, nicht nur zaehlen."""
    td, base = index_repo()
    p = subprocess.run([sys.executable, str(PREFLIGHT), "--root", td, "--json"],
                       capture_output=True, text=True)
    js = json.loads(p.stdout)
    punkte = {z["punkt"]: z for z in js["checks"]}
    check("P ohne Schluessel ist der Lauf NICHT bereit (Exit 1)",
          p.returncode == 1 and js["ready"] is False, f"Exit {p.returncode}")
    check("P der fehlende Schluessel wird als blockierend benannt",
          punkte.get("Reviewer-Schluessel", {}).get("blockierend") is True
          and "env" in punkte["Reviewer-Schluessel"]["fix"],
          json.dumps(punkte.get("Reviewer-Schluessel"))[:160])
    check("P ein fehlendes Testkommando wird als blockierend benannt — sonst endet "
          "jede Lieferung bei CHANGES_REQUIRED",
          punkte.get("Testkommando", {}).get("blockierend") is True,
          json.dumps(punkte.get("Testkommando"))[:140])
    check("P die Familientrennung wird geprueft, bevor irgendetwas laeuft",
          "Familientrennung" in punkte)
    check("P ein fehlender Kontextindex blockiert NICHT",
          punkte.get("Kontextindex", {}).get("blockierend") is False,
          json.dumps(punkte.get("Kontextindex"))[:140])
    check("P ein fehlendes Builder-Kommando blockiert NICHT — Handbetrieb ist gueltig",
          punkte.get("Builder-Kommando", {}).get("blockierend") is False,
          json.dumps(punkte.get("Builder-Kommando"))[:140])

    env = {**os.environ, "DEVOS_REVIEWER_API_KEY": SCHLUESSEL,
           "DEVOS_REVIEWER_MODEL": "gpt-5", "DEVOS_BUILDER_MODEL": "claude-opus-5"}
    p = subprocess.run([sys.executable, str(PREFLIGHT), "--root", td, "--json",
                        "--tests", "python3 -c \"print(1)\""],
                       capture_output=True, text=True, env=env)
    js = json.loads(p.stdout)
    punkte = {z["punkt"]: z for z in js["checks"]}
    check("P mit Schluessel, beiden Modellen und gruenem Test ist der Lauf bereit",
          p.returncode == 0 and js["ready"] is True,
          json.dumps([z["punkt"] for z in js["checks"] if z["blockierend"]]))
    check("P der Schluessel wird NIE ausgegeben, nur seine Laenge",
          SCHLUESSEL not in p.stdout and SCHLUESSEL not in p.stderr)
    check("P nachgewiesene Familientrennung wird als solche ausgewiesen",
          punkte["Familientrennung"]["zustand"] == "ok",
          json.dumps(punkte["Familientrennung"])[:140])

    env2 = {**env, "DEVOS_REVIEWER_MODEL": "claude-sonnet-5"}
    p = subprocess.run([sys.executable, str(PREFLIGHT), "--root", td, "--json",
                        "--tests", "python3 -c \"print(1)\""],
                       capture_output=True, text=True, env=env2)
    js = json.loads(p.stdout)
    check("P gleiche Modellfamilie auf beiden Seiten blockiert die Bereitschaft",
          p.returncode == 1 and any(z["punkt"] == "Familientrennung" and z["blockierend"]
                                    for z in js["checks"]),
          f"Exit {p.returncode}")


def index_repo() -> tuple[str, str]:
    """Ein Repo mit Register, Behauptung und zwei Commits."""
    td = tempfile.mkdtemp(prefix="devos-idx-")
    git(td, "init", "-q", ".")
    git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
    (Path(td) / ".devos.json").write_text(json.dumps({
        "project": "p", "id_pattern": r"\b(XY-\d{3})\b",
        "registers": {"XY": {"path": "reg.md", "kind": "heading",
                             "heading_pattern": r"^#{1,4}\s+XY-\d{3}\b"}}}), encoding="utf-8")
    (Path(td) / "reg.md").write_text("## XY-007 — Erste Norm\n\nWortlaut.\n\n"
                                     "## XY-008 — Zweite Norm\n\nAnderer Wortlaut.\n",
                                     encoding="utf-8")
    (Path(td) / "a.py").write_text("x = 1\n", encoding="utf-8")
    (Path(td) / "notizen.md").write_text("Dieser Code satisfies XY-007 und XY-008.\n",
                                         encoding="utf-8")
    git(td, "add", "."); git(td, "commit", "-qm", "erst")
    (Path(td) / "a.py").write_text("x = 2  # betrifft XY-007\n", encoding="utf-8")
    git(td, "add", "."); git(td, "commit", "-qm", "zweit")
    return td, git(td, "rev-parse", "HEAD").stdout.strip()


def paket(td: str, extra: list[str] | None = None, out: str = "rev") -> dict | None:
    tk = Path(td) / "T.md"
    tk.write_text("# T-IDX — Probe\n\n## Acceptance\n- XY-007 gilt weiterhin\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                        "--base", "HEAD~1", "--head", "HEAD", "--out", out] + (extra or []),
                       capture_output=True, text=True,
                       env={**os.environ, "DEVOS_BUILDER_MODEL": "claude-opus-5"})
    f = Path(td) / out / "review_context.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def indexproben() -> None:
    td, head = index_repo()
    subprocess.run([sys.executable, str(INDEX), "build", "--root", td, "--rev", "HEAD"],
                   capture_output=True, text=True)

    c = paket(td)
    g = (c or {}).get("graph") or {}
    check("X1 jeder Treffer nennt Revision UND Fundstelle",
          g.get("used") is True and g["hits"]
          and all(h.get("rev") == head and h.get("path") and h.get("line") for h in g["hits"]),
          json.dumps(g.get("hits", [])[:1])[:160])
    check("X1 eine im Text BEHAUPTETE Beziehung wird als Behauptung gefuehrt",
          any(h.get("kind") == "claim" for h in g["hits"]),
          str([h.get("kind") for h in g["hits"]][:6]))
    md = (Path(td) / "rev" / "REVIEW-REQUEST.md").read_text(encoding="utf-8")
    check("X1 das Paket sagt dem Reviewer, dass der Index den Pruefumfang NICHT begrenzt",
          "begrenzt den Pruefumfang nicht" in md)

    # Der Index darf nichts wegnehmen: derselbe Diff mit und ohne.
    c_ohne = paket(td, ["--graph", "off"], out="rev_off")
    check("X7 der Index kuerzt weder Diff noch Bestandsliste",
          c_ohne["diff_included"] == c["diff_included"]
          and c_ohne["inventory"]["count"] == c["inventory"]["count"],
          f"{c_ohne['diff_included']} vs {c['diff_included']}")
    check("X6 --graph off erzeugt einen sauberen Vergleichslauf ohne Hinweise",
          c_ohne["graph"]["used"] is False and not c_ohne["graph"]["hits"]
          and "abgeschaltet" in c_ohne["graph"]["why"], json.dumps(c_ohne["graph"])[:140])

    # Veralteter Index: wird NICHT benutzt und steht in omitted.
    (Path(td) / "neu.md").write_text("XY-008 kommt neu dazu\n", encoding="utf-8")
    git(td, "add", "."); git(td, "commit", "-qm", "dritt")
    p = subprocess.run([sys.executable, str(INDEX), "status", "--root", td, "--rev", "HEAD"],
                       capture_output=True, text=True)
    check("X2 status erkennt den veralteten Index und endet mit Exit 1",
          p.returncode == 1 and "VERALTET" in p.stdout, p.stdout.strip()[:140])
    c2 = paket(td, out="rev2")
    check("X2 ein veralteter Index wird NICHT benutzt",
          c2["graph"]["used"] is False and c2["graph"]["stale"] is True,
          json.dumps(c2["graph"])[:160])
    check("X2 und das steht in `omitted`, nicht nur im Log",
          any("Kontextindex" in o["path"] for o in c2["omitted"]),
          json.dumps(c2["omitted"])[:200])

    # Fehlender Index blockiert nichts.
    (Path(td) / "work" / "index" / "context-index.json").unlink()
    c3 = paket(td, out="rev3")
    check("X5 ohne Index entsteht das Paket unveraendert — der Index ist keine Voraussetzung",
          c3 is not None and c3["graph"]["used"] is False
          and c3["diff_included"] == c2["diff_included"], json.dumps((c3 or {}).get("graph"))[:140])

    # Externer Anbieter: Vertrag wird hart geprueft.
    def anbieter(payload: str) -> str:
        sk = Path(td) / "anbieter.py"
        sk.write_text("import sys\nsys.stdin.read()\nsys.stdout.write(%r)\n" % payload,
                      encoding="utf-8")
        return f"{sys.executable} {sk}"

    h2 = git(td, "rev-parse", "HEAD").stdout.strip()
    ohne_ort = json.dumps({"source": "fremd", "built_for_rev": h2, "stale": False,
                           "hits": [{"id": "XY-007", "why": "steht irgendwo"}]})
    c4 = paket(td, out="rev4") if False else None
    os.environ["DEVOS_GRAPH_CMD"] = anbieter(ohne_ort)
    try:
        c4 = paket(td, out="rev4")
        g4 = c4["graph"]
        check("X3 Anbieter-Treffer ohne Fundstelle/Revision werden VERWORFEN, nicht benutzt",
              g4["used"] is True and not g4["hits"] and len(g4["rejected"]) == 1,
              json.dumps(g4)[:200])
        check("X3 die Verwerfung steht in `omitted` — der Mensch sieht sie",
              any("Kontexthinweise" in o["path"] for o in c4["omitted"]),
              json.dumps(c4["omitted"])[:200])

        falsche_rev = json.dumps({"source": "fremd", "built_for_rev": h2, "stale": False,
                                  "hits": [{"id": "XY-007", "path": "reg.md", "line": 1,
                                            "rev": "0" * 40}]})
        os.environ["DEVOS_GRAPH_CMD"] = anbieter(falsche_rev)
        c5 = paket(td, out="rev5")
        check("X4 ein Treffer aus einer ANDEREN Revision wird verworfen",
              not c5["graph"]["hits"] and c5["graph"]["rejected"], json.dumps(c5["graph"])[:200])

        os.environ["DEVOS_GRAPH_CMD"] = f"{sys.executable} -c 'import sys; sys.exit(3)'"
        c6 = paket(td, out="rev6")
        check("X9 ein kaputter Anbieter blockiert das Paket nicht",
              c6 is not None and c6["graph"]["used"] is False
              and c6["diff_included"] == c2["diff_included"],
              json.dumps((c6 or {}).get("graph"))[:160])

        os.environ["DEVOS_GRAPH_CMD"] = anbieter(json.dumps(
            {"source": "fremd", "built_for_rev": h2, "stale": True, "hits": []}))
        c7 = paket(td, out="rev7")
        check("X2 ein Anbieter, der sich selbst als veraltet meldet, wird nicht benutzt",
              c7["graph"]["used"] is False and c7["graph"]["stale"] is True,
              json.dumps(c7["graph"])[:160])
    finally:
        os.environ.pop("DEVOS_GRAPH_CMD", None)


def messproben() -> None:
    """Die Messung muss den Unterschied zwischen 'null' und 'nicht gemessen' halten."""
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "a.txt").write_text("x\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        b = git(td, "rev-parse", "HEAD").stdout.strip()
        (Path(td) / "b.txt").write_text("y\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        h = git(td, "rev-parse", "HEAD").stdout.strip()
        log = Path(td) / "d.jsonl"
        log.write_text("\n".join(json.dumps(x) for x in [
            {"delivery": "M1", "event": "task_opened", "at": "2026-09-15T09:00:00Z", "base": b,
             "scope": "full"},
            {"delivery": "M1", "event": "builder_minutes", "minutes": 60},
            {"delivery": "M1", "event": "reviewer_minutes", "minutes": 10},
            {"delivery": "M1", "event": "model_usage", "prompt_tokens": 48000,
             "completion_tokens": 3100, "cost_usd": 0.09},
            {"delivery": "M1", "event": "accepted", "at": "2026-09-15T13:00:00Z", "commit": h},
            {"delivery": "M2", "event": "task_opened", "at": "2026-09-16T09:00:00Z", "base": b,
             "scope": "full"},
            {"delivery": "M2", "event": "human_minutes", "minutes": 30},
            {"delivery": "M2", "event": "builder_minutes", "minutes": 70},
            {"delivery": "M2", "event": "escaped_defect", "what": "Nullteiler in der Bewertung",
             "found_at": "2026-10-02", "where": "bewertung.py:40"},
            {"delivery": "M2", "event": "accepted", "at": "2026-09-16T11:00:00Z", "commit": h},
        ]), encoding="utf-8")
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(log), "--root", td, "--json"],
                           capture_output=True, text=True)
        js = json.loads(p.stdout)
        m1 = next(x for x in js["deliveries"] if x["delivery"] == "M1")
        m2 = next(x for x in js["deliveries"] if x["delivery"] == "M2")
        check("M nicht gebuchte Menschenzeit ist NICHT null Prozent",
              m1["human_share"] is None and m1["human_booked"] is False, str(m1["human_share"]))
        check("M eine Lieferung ohne gebuchte Menschenzeit gilt nicht als vollstaendig gemessen",
              m1["complete"] is False and m2["complete"] is True,
              f"M1={m1['complete']} M2={m2['complete']}")
        check("M Modellnutzung wird in Token und USD gefuehrt",
              m1["prompt_tokens"] == 48000 and m1["cost_usd"] == 0.09,
              json.dumps({k: m1[k] for k in ("prompt_tokens", "cost_usd")}))
        check("M nachtraeglich entdeckte Fehler werden je Lieferung gefuehrt",
              m2["escaped_defect_count"] == 1
              and m2["escaped_defects"][0]["where"] == "bewertung.py:40",
              json.dumps(m2["escaped_defects"])[:140])
        txt = subprocess.run([sys.executable, str(METRICS), "--log", str(log), "--root", td],
                             capture_output=True, text=True).stdout
        check("M der Bericht benennt die ungebuchte Menschenzeit als Befund",
              "Menschenzeit nicht" in txt and "nicht gebucht" in txt)
        check("M der Bericht fuehrt die spaeter gefundenen Fehler eigens auf",
              "Nachtraeglich entdeckte Fehler" in txt and "bewertung.py:40" in txt)

    # --from-run: erzeugt das Maschinelle, erfindet das Menschliche nicht
    td, base = orch_repo(BUILDER_ZWEI_RUNDEN)
    orch_run(td, base, orch_urteil())
    p = subprocess.run([sys.executable, str(METRICS), "--from-run",
                        str(Path(td) / "work" / "runs")], capture_output=True, text=True)
    zeilen = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]
    ereignisse = [z.get("event") for z in zeilen if "event" in z]
    check("M --from-run erzeugt die maschinell messbaren Ereignisse",
          p.returncode == 0 and "task_opened" in ereignisse and "review_round" in ereignisse
          and "model_usage" in ereignisse, f"Exit {p.returncode} {ereignisse}")
    check("M --from-run schreibt NIE `accepted` — PASS ist keine Abnahme",
          "accepted" not in ereignisse, str(ereignisse))
    check("M --from-run schreibt keine erfundene Menschenzeit",
          "human_minutes" not in ereignisse, str(ereignisse))
    check("M --from-run sagt, was der Mensch nachtragen muss",
          any("human_minutes" in (z.get("_comment") or "") for z in zeilen),
          json.dumps(zeilen[-1])[:160])


def main() -> int:
    # Hermetisch: eine vorhandene ~/.config/devos/env wuerde sonst Schluessel und
    # Modellnamen in jede Probe tragen. Ein Eigentest, der je nach Rechner andere
    # Umgebung sieht, prueft nicht das Werkzeug, sondern den Rechner.
    os.environ["DEVOS_ENV_FILE"] = str(Path(tempfile.gettempdir()) / "devos-gibt-es-nicht.env")
    for v in ("DEVOS_REVIEWER_API_KEY", "DEVOS_REVIEWER_MODEL", "DEVOS_REVIEWER_BASE_URL",
              "DEVOS_BUILDER_MODEL", "DEVOS_BUILDER_CMD", "DEVOS_GRAPH_CMD",
              "DEVOS_REVIEWER_PRICE_IN", "DEVOS_REVIEWER_PRICE_OUT"):
        os.environ.pop(v, None)
    print("DevOS Eigentest\n")

    print("Gate — Grundregeln:")
    code, out = run_gate(base_result(), ok_context())
    check("sauberes PASS mit isoliert bestandenen Tests => MERGEABLE_PENDING_HUMAN, Exit 0",
          code == 0 and out.get("gate", "").startswith("MERGEABLE"), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(blocking=[{"what": "Luecke", "where": "a.py:4", "why": "kein Pfad"}]), ok_context())
    check("PASS mit blockierendem Befund => CHANGES_REQUIRED, Exit 1", code == 1, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(context_sufficient=False, missing_context=["Diff fehlte"]), ok_context())
    check("context_sufficient=false => INSUFFICIENT_CONTEXT, Exit 2",
          code == 2 and "INSUFFICIENT_CONTEXT" in out.get("gate", ""), f"Exit {code} {out.get('gate')}")

    print("\nF01 — Kontextbindung und Identitaet (Reviewer-Befund Runde 1):")
    code, out = run_gate(base_result())
    check("F01 ohne --context ist PASS unerreichbar", code != 0, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ctx_path="/gibt/es/nicht.json")
    check("F01 --context zeigt ins Leere => nicht Exit 0", code != 0, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(task_id="ANDERE-TASK"), ok_context())
    check("F01 task_id weicht vom Paket ab => INSUFFICIENT_CONTEXT",
          code == 2 and "INSUFFICIENT_CONTEXT" in out.get("gate", ""), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(reviewed_range={"base": BASE_A, "head": HEAD_B}), ok_context())
    check("F01 zwei SHAs mit gleichem 12-Zeichen-Praefix sind NICHT gleich",
          code == 2, f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(reviewed_range={"base": BASE_A, "head": ""}), ok_context())
    check("F01 leerer head => INSUFFICIENT_CONTEXT", code == 2, f"Exit {code} {out.get('gate')}")

    print("\nF02 — Schema und Acceptance als Gate-Bedingungen:")
    r = base_result(); r["reviewed"]["tests"]["judged"] = "banana"
    code, _ = run_gate(r, ok_context())
    check("F02 ungueltiger Enumwert => INVALID_RESULT, Exit 3", code == 3, f"Exit {code}")
    code, _ = run_gate(base_result(blocking={}), ok_context())
    check("F02 blocking als Objekt statt Liste => INVALID_RESULT", code == 3, f"Exit {code}")
    code, _ = run_gate(base_result(unbekanntes_feld=1), ok_context())
    check("F02 unbekanntes Feld => INVALID_RESULT (additionalProperties)", code == 3, f"Exit {code}")
    r = base_result(); r["reviewed"]["acceptance_items"] = [{"item": ACC, "verdict": "not_met", "why": "nein"}]
    code, out = run_gate(r, ok_context())
    check("F02 PASS mit Acceptance not_met => CHANGES_REQUIRED", code == 1, f"Exit {code} {out.get('gate')}")
    r = base_result(); r["reviewed"]["acceptance_items"] = [{"item": ACC, "verdict": "unverifiable"}]
    code, out = run_gate(r, ok_context())
    check("F02 Acceptance unverifiable => INSUFFICIENT_CONTEXT", code == 2, f"Exit {code} {out.get('gate')}")

    print("\nF03 — Tests gehoeren zum geprueften Stand:")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": True, "isolated": False, "exit_code": 0, "ran_against": HEAD_A}))
    check("F03 nicht isolierter Testlauf => PASS ausgeschlossen", code == 1, f"Exit {code} {out.get('gate')}")
    code, _ = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": False, "isolated": True, "exit_code": 1, "ran_against": HEAD_A}))
    check("F03 fehlgeschlagene Tests => CHANGES_REQUIRED", code == 1, f"Exit {code}")
    code, _ = run_gate(base_result(), ok_context(
        tests={"ran": False, "passed": False, "isolated": False, "exit_code": None,
               "why": "kein Testkommando uebergeben"}))
    check("F03 keine Tests gelaufen => CHANGES_REQUIRED", code == 1, f"Exit {code}")

    print("\nN02 — Governance-Konflikt schlaegt jeden Ausgangsstatus:")
    code, out = run_gate(base_result(status="CHANGES_REQUIRED",
                                     blocking=[{"what": "x", "where": "y", "why": "z"}],
                                     governance_conflicts=[{"id": "D-144", "what": "Rechte erweitert"}]),
                         ok_context())
    check("N02 CHANGES_REQUIRED + Governance-Konflikt => CONFLICT",
          "CONFLICT" in out.get("gate", "") and code == 2, f"Exit {code} {out.get('gate')}")

    print("\nS — das Werkzeug traegt keinen fremden Projektinhalt in sein eigenes Repo:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "a.py").write_text("x = 1\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        (Path(td) / "a.py").write_text("x = 2\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        tk = Path(td) / "T.md"; tk.write_text("# T-1 — Probe\n\n## Acceptance\n- gilt\n", encoding="utf-8")
        ziel = HERE.parent / "work" / "fremd-probe"
        p = subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                            "--base", "HEAD~1", "--head", "HEAD", "--out", str(ziel)],
                           capture_output=True, text=True)
        check("S ein Paket eines fremden Projekts darf nicht ins DevOS-Repo geschrieben werden",
              p.returncode == 1 and not ziel.exists(), f"Exit {p.returncode} {p.stderr.strip()[:160]}")

    print("\nG01 — der Kontext wird strukturell geprueft, nicht nur geparst:")
    code, out = run_gate(base_result(), None, ctx_raw="[]")
    check("G01 Kontext als JSON-Liste => Befund statt Absturz",
          code == 2 and out.get("gate") != "GATE HAT NICHT GEURTEILT",
          f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": "false", "isolated": True, "exit_code": 0, "ran_against": HEAD_A}))
    check("G01 passed als String \"false\" (in Python wahr) => Typfehler erkannt",
          code == 2 and "tests.passed" in json.dumps(out), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ok_context(sonderfeld=1))
    check("G01 unbekanntes Feld im Kontext => INSUFFICIENT_CONTEXT (additionalProperties)",
          code == 2 and "sonderfeld" in json.dumps(out), f"Exit {code}")
    c = ok_context(); c.pop("tests")
    code, out = run_gate(base_result(), c)
    check("G01 Kontext ohne tests-Block => Pflichtfeld fehlt, PASS unerreichbar", code == 2, f"Exit {code}")

    print("\nG02 — der Reviewer muss bewerten, was gefordert war:")
    ctx3 = ok_context()
    ctx3["task"]["acceptance"] = [ACC, "der Punkt B gilt nachweislich", "der Punkt C gilt nachweislich"]
    r = base_result(); r["reviewed"]["acceptance_items"] = []
    code, out = run_gate(r, ctx3)
    check("G02 kein einziger Punkt bewertet, drei gefordert => PASS unerreichbar",
          code == 2 and len(out.get("acceptance_uncovered", [])) == 3, f"Exit {code}")
    r = base_result(); r["reviewed"]["acceptance_items"] = [{"item": ACC, "verdict": "met"}]
    code, out = run_gate(r, ctx3)
    check("G02 1 von 3 bewertet => die zwei offenen werden BENANNT",
          code == 2 and out.get("acceptance_uncovered") == ["der Punkt B gilt nachweislich",
                                                            "der Punkt C gilt nachweislich"],
          json.dumps(out.get("acceptance_uncovered"))[:120])
    r = base_result()
    r["reviewed"]["acceptance_items"] = [{"item": ACC, "verdict": "met"},
                                         {"item": "etwas voellig anderes als gefordert", "verdict": "met"}]
    code, out = run_gate(r, ok_context())
    check("G02 Bewertung ohne Entsprechung in der Task => PASS unerreichbar",
          code == 2 and out.get("acceptance_unbound") == ["etwas voellig anderes als gefordert"],
          json.dumps(out.get("acceptance_unbound"))[:120])
    r = base_result()
    r["reviewed"]["acceptance_items"] = [{"item": "  Der Punkt A gilt NACHWEISLICH.  ", "verdict": "met"}]
    code, out = run_gate(r, ok_context())
    check("G02 Gross-/Kleinschreibung und Satzzeichen entscheiden nicht ueber die Abdeckung",
          code == 0, f"Exit {code} {out.get('gate')}")
    c = ok_context(); c["task"]["acceptance"] = []
    r = base_result(); r["reviewed"]["acceptance_items"] = []
    code, out = run_gate(r, c)
    check("G02 Task ohne jeden Acceptance-Punkt => es gibt nichts zu pruefen, PASS unerreichbar",
          code == 2, f"Exit {code} {out.get('gate')}")

    print("\nG03 — der Testnachweis wird nachgerechnet, nicht geglaubt:")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": True, "isolated": True, "exit_code": 1, "ran_against": HEAD_A}))
    check("G03 exit_code 1 bei passed=true => Widerspruch, PASS unerreichbar",
          code == 2 and "widerspricht" in json.dumps(out), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": False, "isolated": True, "exit_code": 0, "ran_against": HEAD_A}))
    check("G03 exit_code 0 bei passed=false => ebenfalls Widerspruch", code == 2, f"Exit {code}")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": True, "isolated": True, "exit_code": 0, "ran_against": HEAD_B}))
    check("G03 Tests gegen eine andere Revision als den geprueften Head => PASS unerreichbar",
          code == 2 and "andere Revision" in json.dumps(out), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": True, "isolated": True, "exit_code": 0}))
    check("G03 Testlauf ohne Revisionsangabe => an nichts gebunden, PASS unerreichbar",
          code == 2, f"Exit {code}")
    code, out = run_gate(base_result(), ok_context(
        tests={"ran": True, "passed": True, "isolated": True, "ran_against": HEAD_A}))
    check("G03 Testlauf ohne Exit-Code => `passed` allein ist kein Nachweis",
          code == 2 and "ohne Exit-Code" in json.dumps(out), f"Exit {code}")

    print("\nG04 — die Familientrennung ist die Gegenmassnahme und wird selbst geprueft:")
    gleich = ok_dispatch(reviewer={"model": "claude-sonnet-5", "family": "anthropic",
                                   "declared_via": "Namenstabelle"})
    code, out = run_gate(base_result(), ok_context(), dispatch=gleich)
    check("G04 Reviewer aus der Builder-Familie => INDEPENDENCE_UNPROVEN, kein PASS",
          code == 2 and "INDEPENDENCE_UNPROVEN" in out.get("gate", ""), f"Exit {code} {out.get('gate')}")
    selbst = ok_dispatch(reviewer={"model": "claude-opus-5", "family": "anthropic",
                                   "declared_via": "Namenstabelle"})
    code, out = run_gate(base_result(), ok_context(), dispatch=selbst)
    check("G04 dasselbe Modell auf beiden Seiten => kein PASS",
          code == 2 and "dasselbe Modell" in json.dumps(out), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ok_context(
        builder={"model": None, "family": None, "declared_via": None}), dispatch=None)
    check("G04 gar kein Nachweis => nicht nachgewiesen ist nicht dasselbe wie getrennt",
          code == 2 and "nicht nachgewiesen" in json.dumps(out), f"Exit {code} {out.get('gate')}")
    code, out = run_gate(base_result(), ok_context(
        builder={"model": "eigenmodell-7", "family": None, "declared_via": "Namenstabelle"}),
        dispatch=ok_dispatch(reviewer={"model": "fremdmodell-3", "family": None,
                                       "declared_via": "Namenstabelle"}))
    check("G04 zwei unbekannte Modellnamen gelten NICHT als verschieden",
          code == 2 and (out.get("independence") or {}).get("separated") is None,
          f"Exit {code} {json.dumps(out.get('independence'))[:120]}")
    code, out = run_gate(base_result(reviewer={"model": "gpt-5"}), ok_context(), dispatch=None)
    u = out.get("independence") or {}
    check("G04 Selbstauskunft des Reviewers zaehlt, wird aber als ungemessen ausgewiesen",
          code == 0 and u.get("separated") is True and "Selbstauskunft" in u.get("evidence", ""),
          f"Exit {code} {json.dumps(u)[:140]}")
    code, out = run_gate(base_result(), ok_context())
    check("G04 nachgewiesene Trennung blockiert nicht — das Gate ist kein Dauer-Nein",
          code == 0 and (out.get("independence") or {}).get("separated") is True,
          f"Exit {code} {out.get('gate')}")

    print("\nG05 — der Schemapruefer behauptet nicht mehr, als er durchsetzt:")
    sys.path.insert(0, str(HERE))
    import jsonschema_mini as J   # noqa: E402
    zuviel = [k for k in ("allOf", "anyOf", "oneOf", "not", "patternProperties") if k in J.SUPPORTED]
    check("G05 SUPPORTED nennt kein Konstrukt, das validate() ignoriert", not zuviel, str(zuviel))
    offen = []
    for sd in sorted((HERE.parent / "schema").glob("*.json")):
        offen += [f"{sd.name}: {x}" for x in J.unsupported_keywords(json.loads(sd.read_text(encoding="utf-8")))]
    check("G05 die eigenen Schemata benutzen nur durchgesetzte Konstrukte", not offen, str(offen[:3]))
    check("G05 type-Liste [\"integer\",\"null\"] laesst null zu und weist str ab",
          not J.validate({"x": None}, {"type": "object", "properties": {"x": {"type": ["integer", "null"]}}})
          and J.validate({"x": "s"}, {"type": "object", "properties": {"x": {"type": ["integer", "null"]}}}))

    print("\nF03/F04 — Paketerzeugung gegen ein echtes Repo:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "alt.py").write_text("print(1)\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        git(td, "mv", "alt.py", "neu.py")
        (Path(td) / "gross.txt").write_text("x\n" * 400, encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        t = Path(td) / "TASK-T.md"
        t.write_text("# TASK-T — Probe\n\n## Acceptance\n- etwas gilt\n- ein Punkt, der ueber\n"
                     "  zwei Zeilen laeuft\n\n## Forbidden\n- etwas nicht\n\n"
                     "## Relevante Beschluesse\n- D-144\n", encoding="utf-8")
        # Projektbindung: eigenes Register, eigener Praefix - das Werkzeug kennt
        # keine mahoraga-Pfade mehr.
        (Path(td) / "reg.md").write_text("## XY-007 — Probenorm\n\nDer Wortlaut.\n\n"
                                         "## XY-008 — danach\n", encoding="utf-8")
        # Datei NUR im schmutzigen Arbeitsverzeichnis — im Worktree des Heads darf sie fehlen
        (Path(td) / "nur_lokal.txt").write_text("reparatur\n", encoding="utf-8")
        out_dir = Path(td) / "rev"
        p = subprocess.run([sys.executable, str(REQUEST), "--task", str(t), "--root", td,
                            "--head", "HEAD", "--base", "HEAD~1", "--out", str(out_dir),
                            "--tests", "test -f nur_lokal.txt"], capture_output=True, text=True)
        check("Paketerzeugung laeuft", p.returncode == 0, p.stderr.strip()[:200])
        cf = out_dir / "review_context.json"
        if cf.exists():
            c = json.loads(cf.read_text(encoding="utf-8"))
            md = (out_dir / "REVIEW-REQUEST.md").read_text(encoding="utf-8")
            check("F03 Tests laufen isoliert gegen den geprueften Head",
                  c["tests"].get("isolated") is True and c["tests"].get("ran_against") == c["range"]["head"],
                  json.dumps(c["tests"])[:160])
            check("F03 eine nur lokal vorhandene Datei kann den Test nicht gruen faerben",
                  c["tests"]["passed"] is False, f"passed={c['tests'].get('passed')}")
            check("F03 Schmutzzustand des Arbeitsverzeichnisses wird ausgewiesen",
                  c["tests"].get("worktree_dirty") is True)
            paths = [f["path"] for f in c["files_changed"]]
            check("F04 Umbenennung liefert echte Pfade statt 'alt => neu'",
                  "neu.py" in paths and not any("=>" in x for x in paths), str(paths))
            check("F04 Umbenennung nennt die Herkunft",
                  any(f.get("renamed_from") == "alt.py" for f in c["files_changed"]))
            check("F04 base ist ein voller SHA, kein 'HEAD~1'", len(c["range"]["base"]) == 40, c["range"]["base"])
            check("F04 vollstaendiges Testprotokoll liegt als Artefakt daneben",
                  (out_dir / "test-output.txt").exists() and "output_sha256" in c["tests"])
            check("'omitted' ist Pflichtfeld, auch leer", "omitted" in c)
            check("das eigene Ausgabeverzeichnis wird ausgeschnitten und genannt",
                  not any(x.startswith("rev/") for x in c["diff_included"]))
            check("Acceptance mehrzeilig ungekuerzt",
                  c["task"]["acceptance"][1] == "ein Punkt, der ueber zwei Zeilen laeuft",
                  repr(c["task"]["acceptance"][1:]))
            check("Register-IDs erkannt", "D-144" in c["referenced_ids"])
            check("ohne .devos.json wird das Fehlen der Projektbindung gemeldet, nicht verschwiegen",
                  any(o["path"] == ".devos.json" for o in c["omitted"]), json.dumps(c["omitted"])[:200])
            check("Projektname steht im Kontext", c.get("project") is not None)
            check("Abschnitt 'Nicht mitgeschickt' steht im Paket", "## Nicht mitgeschickt" in md)
            check("volle SHAs stehen im Markdown", c["range"]["head"] in md)
            check("INSUFFICIENT_CONTEXT wird dem Reviewer angeboten", "INSUFFICIENT_CONTEXT" in md)

    print("\nProjektbindung ueber .devos.json — das Werkzeug kennt kein Projekt:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / ".devos.json").write_text(json.dumps({
            "project": "probeprojekt", "id_pattern": r"\b(XY-\d{3})\b",
            "registers": {"XY": {"path": "reg.md", "kind": "heading",
                                 "heading_pattern": r"^#{1,4}\s+XY-\d{3}\b"}}}), encoding="utf-8")
        (Path(td) / "reg.md").write_text("## XY-007 — Probenorm\n\nDer Wortlaut steht hier.\n\n"
                                         "## XY-008 — danach\n", encoding="utf-8")
        (Path(td) / "a.py").write_text("x = 1\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        (Path(td) / "a.py").write_text("x = 2  # betrifft XY-007\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        tk = Path(td) / "T.md"; tk.write_text("# T-1 — Probe\n\n## Acceptance\n- gilt\n", encoding="utf-8")
        od = Path(td) / "rev"
        subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                        "--base", "HEAD~1", "--head", "HEAD", "--out", str(od)],
                       capture_output=True, text=True)
        c = json.loads((od / "review_context.json").read_text(encoding="utf-8"))
        check("Projektname aus .devos.json uebernommen", c.get("project") == "probeprojekt",
              str(c.get("project")))
        check("fremdes ID-Muster erkannt", "XY-007" in c["referenced_ids"], str(c["referenced_ids"]))
        check("Normquelle aus dem konfigurierten Register geholt",
              "XY-007" in c["norm_sources"] and "Der Wortlaut steht hier."
              in c["norm_sources"]["XY-007"]["text"], json.dumps(c["norm_sources"])[:160])
        check("Abschnitt endet an der naechsten gleichrangigen Ueberschrift",
              "XY-008" not in c["norm_sources"].get("XY-007", {}).get("text", ""))

    with tempfile.TemporaryDirectory() as td:
        # ID-Formen ohne Bindestrich (G01) muessen ihr Register genauso finden.
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / ".devos.json").write_text(json.dumps({
            "project": "p", "id_pattern": r"\b(G\d{2})\b",
            "registers": {"G": {"path": "B.md", "kind": "heading",
                                "heading_pattern": r"^#{1,3}\s+G\d{2}\b"}}}), encoding="utf-8")
        (Path(td) / "B.md").write_text("## G01 — Erster\n\nWortlaut eins.\n\n## G02 — Zweiter\n",
                                       encoding="utf-8")
        (Path(td) / "a.py").write_text("x = 1\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        (Path(td) / "a.py").write_text("x = 2  # G01\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        tk = Path(td) / "T.md"; tk.write_text("# T-2 — Probe\n\n## Acceptance\n- gilt\n", encoding="utf-8")
        od = Path(td) / "rev"
        subprocess.run([sys.executable, str(REQUEST), "--task", str(tk), "--root", td,
                        "--base", "HEAD~1", "--head", "HEAD", "--out", str(od)],
                       capture_output=True, text=True)
        c = json.loads((od / "review_context.json").read_text(encoding="utf-8"))
        check("Praefix ist der Buchstabenteil: eine ID ohne Bindestrich findet ihr Register",
              "Wortlaut eins." in c["norm_sources"].get("G01", {}).get("text", ""),
              json.dumps(c.get("norm_sources"))[:160] + " | " + json.dumps(c["omitted"])[:120])

    print("\nF05 — Prozessmessung misst, was sie behauptet:")
    with tempfile.TemporaryDirectory() as td:
        git(td, "init", "-q", ".")
        git(td, "config", "user.email", "t@t"); git(td, "config", "user.name", "t")
        (Path(td) / "a.txt").write_text("x\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "erst")
        b = git(td, "rev-parse", "HEAD").stdout.strip()
        (Path(td) / "b.txt").write_text("eine Zeile\n", encoding="utf-8")
        git(td, "add", "."); git(td, "commit", "-qm", "zweit")
        h = git(td, "rev-parse", "HEAD").stdout.strip()
        log = Path(td) / "d.jsonl"
        log.write_text("\n".join(json.dumps(x) for x in [
            {"delivery": "T1", "event": "task_opened", "at": "2026-09-15T09:00:00Z", "base": b, "scope": "reduced"},
            {"delivery": "T1", "event": "human_minutes", "minutes": 30},
            {"delivery": "T1", "event": "builder_minutes", "minutes": 60},
            {"delivery": "T1", "event": "reviewer_minutes", "minutes": 10},
            {"delivery": "T1", "event": "suspended_artifact", "name": "K1-Rechnung"},
            {"delivery": "T1", "event": "review_round", "blocking": 2, "false_alarms": 1},
            {"delivery": "T1", "event": "accepted", "at": "2026-09-15T13:00:00Z", "commit": h},
            {"delivery": "T2", "event": "task_opened", "at": "2026-09-16T09:00:00Z"},
            {"delivery": "T3", "event": "task_opened", "at": "2026-09-17T09:00:00Z", "base": b},
            {"delivery": "T3", "event": "accepted", "at": "2026-09-17T10:00:00Z", "commit": "kaputteref"},
        ]), encoding="utf-8")
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(log), "--root", td, "--json"],
                           capture_output=True, text=True)
        js = json.loads(p.stdout)
        d1 = next(x for x in js["deliveries"] if x["delivery"] == "T1")
        check("Gesamtzeit summiert", d1["total_minutes"] == 100, str(d1["total_minutes"]))
        check("Menschenanteil berechnet", d1["human_share"] == 0.3, str(d1["human_share"]))
        check("Durchlaufzeit berechnet", d1["lead_time_hours"] == 4.0, str(d1["lead_time_hours"]))
        check("Treffergenauigkeit der Blocker berechnet", d1["blocker_precision"] == 0.5, str(d1["blocker_precision"]))
        check("F05 eine neue Datei mit einer Zeile ergibt 1 geaenderte Zeile, nicht 2",
              d1["changed_lines"] == 1, str(d1["changed_lines"]))
        check("F05 Dateizahl wird getrennt von der Zeilenzahl gefuehrt", d1["changed_files"] == 1,
              str(d1["changed_files"]))
        check("F05 nur eroeffnete Task zaehlt nicht als abgeschlossen", js["n_complete"] == 1,
              f"n_complete={js['n_complete']}")
        d3 = next(x for x in js["deliveries"] if x["delivery"] == "T3")
        check("F05 ungueltige git-Ref wird ausgewiesen, nicht zu 0",
              d3["git_error"] is not None and d3["changed_lines"] is None, str(d3.get("git_error"))[:120])
        check("F05 ausgesetzte Pflichtartefakte werden als offene Last gefuehrt",
              d1["suspended_artifacts"] == ["K1-Rechnung"])
        txt = subprocess.run([sys.executable, str(METRICS), "--log", str(log), "--root", td],
                             capture_output=True, text=True).stdout
        check("F05 kein Urteil ueber den Vollumfang aus einem reduzierten Pilot",
              "KEIN URTEIL" in txt and "WIDERLEGT" not in txt)
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(Path(td) / "weg.jsonl")],
                           capture_output=True, text=True)
        check("fehlendes Log ist ein Befund, kein stilles OK (Exit != 0)", p.returncode != 0, f"Exit {p.returncode}")

        kaputt = Path(td) / "kaputt.jsonl"
        kaputt.write_text('{"_comment":"zulaessig"}\n{"event":"task_opened"}\n', encoding="utf-8")
        p = subprocess.run([sys.executable, str(METRICS), "--log", str(kaputt), "--root", td],
                           capture_output=True, text=True)
        check("kaputte Logzeile ist ein Befund, kein Absturz",
              p.returncode == 1 and "BEFUND" in p.stderr and "Traceback" not in p.stderr,
              f"Exit {p.returncode} {p.stderr.strip()[:120]}")

    print("\nN01 — Transport: der Mensch traegt das Paket nicht mehr:")
    transport_proben()

    print("\nE2E — die ganze Kette an einem echten Repo:")
    e2e_proben()

    print("\nO — Orchestrator: Runden, Budget, Zustand, Fortsetzung:")
    orchestrator_proben()

    print("\nR — Registerpruefer: die Regeln des Projekts, nicht die eigenen:")
    registerproben()

    print("\nP — Bereitschaft: der Bericht sagt, was fehlt, nicht nur dass etwas fehlt:")
    preflight_proben()

    print("\nX — Kontextindex: findet, entscheidet nicht, begrenzt nichts:")
    indexproben()

    print("\nY — Graphify (MCP): der Dienst schlaegt vor, das Original entscheidet:")
    graphify_proben()

    print("\nM — Messung: was eine Maschine nicht messen kann, erfindet sie nicht:")
    messproben()

    print(f"\nbestanden: {len(PASSED)} von {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("\nFEHLGESCHLAGEN:")
        for n, d in FAILED:
            print(f"  - {n}  {d}")
        print("\nverdict: FEHLGESCHLAGEN")
        return 1
    print("verdict: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
