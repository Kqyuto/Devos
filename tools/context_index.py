#!/usr/bin/env python3
"""Kontextindex — findet Fundstellen. Massgeblich bleibt das Originalartefakt.

    python3 devos/tools/context_index.py build  --root . --rev HEAD
    python3 devos/tools/context_index.py query  --root . --ids D-001,OQ-003
    python3 devos/tools/context_index.py status --root . --rev HEAD

Das ist die austauschbare Graphify-Schicht. Sie ist ausdruecklich KEINE
Voraussetzung: faellt sie weg, laeuft das Verfahren unveraendert weiter, nur mit
weniger Hinweisen. Ein Index, ohne den nichts geht, ist keine Erweiterung mehr,
sondern eine zweite Wahrheit.

Fuenf Regeln, und sie sind hier Code, nicht Absicht:

  1. **Der Index findet, er entscheidet nicht.** Jeder Treffer ist ein Zeiger auf
     eine Fundstelle. Das Urteil faellt am Original.
  2. **Jeder Treffer nennt Revision UND Fundstelle.** Ein Treffer ohne beides
     wird VERWORFEN, gezaehlt und gemeldet — nie benutzt. Das gilt auch und
     gerade fuer einen externen Anbieter.
  3. **Ein veralteter Index wird erkannt und NICHT benutzt.** `status` vergleicht
     die Indexrevision mit der geprueften und nennt die Dateien, die sich seither
     geaendert haben. Ein unvollstaendiger Index nennt, was er ausgelassen hat.
  4. **Der Index begrenzt den Pruefumfang nicht.** Das Review-Paket traegt
     unabhaengig davon den vollstaendigen Diff, die Bestandsliste und das Recht
     des Reviewers, Quellen nachzufordern. Der Index kommt hinzu, er ersetzt
     nichts.
  5. **Eine Builder-Beziehung ist eine Behauptung.** Dieses Werkzeug leitet
     niemals selbst `satisfies`, `implements` oder `erfuellt` ab. Steht so etwas
     im Text, wird es als `claim` gefuehrt und als unbelegt markiert.

Externer Anbieter (das eigentliche Graphify, sobald es feststeht):

    .devos.json:  {"graph": {"cmd": "graphify devos-adapter", "name": "graphify"}}
    oder:         DEVOS_GRAPH_CMD="graphify devos-adapter"

Vertrag: das Kommando bekommt auf stdin

    {"op": "query", "rev": "<voller SHA>", "ids": ["D-001"], "depth": 1}

und antwortet auf stdout

    {"source": "graphify", "built_for_rev": "<voller SHA>", "stale": false,
     "hits": [{"id": "D-001", "path": "...", "line": 42, "rev": "<voller SHA>",
               "why": "Definition", "kind": "definition"}],
     "coverage": {"files_indexed": 120, "files_skipped": []}}

Alles, was diesen Vertrag verletzt, wird verworfen und gemeldet — nicht geraten.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from project_config import Config      # noqa: E402

VERSION = "devos/tools/context_index.py@1.0"
STANDARD_INDEX = "work/index/context-index.json"
TEXTENDUNGEN = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".rst", ".toml", ".cfg"}
MAX_DATEI_BYTES = 400_000

# Woerter, mit denen eine Behauptung ueber eine Beziehung formuliert wird. Sie
# werden ERKANNT, nicht geglaubt: der Treffer bekommt kind="claim".
BEHAUPTUNG = re.compile(
    r"(?i)\b(satisfies|implements|erf[uü]llt|umsetzt|setzt\s+um|deckt\s+ab|covers)\b")


def git(root: str, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def voller_sha(root: str, rev: str) -> str | None:
    rc, out = git(root, "rev-parse", rev)
    return out.strip() if rc == 0 and out.strip() else None


# ----------------------------------------------------------------- eingebaut

def bauen(root: str, rev: str, cfg: Config) -> dict:
    """Index aus der REVISION, nicht aus dem Arbeitsverzeichnis.

    Aus dem Arbeitsverzeichnis gebaut wuerde der Index Fundstellen nennen, die
    es im geprueften Stand nicht gibt — und genau das soll Regel 2 verhindern.
    """
    sha = voller_sha(root, rev)
    if not sha:
        raise SystemExit(f"[2] Revision {rev!r} nicht aufloesbar")
    rc, liste = git(root, "ls-tree", "-r", "--name-only", sha)
    if rc != 0:
        raise SystemExit(f"[2] Bestand von {sha[:12]} nicht lesbar")
    pfade = [p for p in liste.splitlines() if p]

    muster = re.compile(cfg.id_pattern)
    heading = re.compile(r"^#{1,6}\s+(\S+)\s*(.*)$")
    knoten: dict[str, dict] = {}
    kanten: list[dict] = []
    gelesen, ausgelassen = 0, []

    for pfad in pfade:
        if Path(pfad).suffix.lower() not in TEXTENDUNGEN:
            ausgelassen.append({"path": pfad, "why": "keine indizierte Textendung"})
            continue
        rc, inhalt = git(root, "show", f"{sha}:{pfad}")
        if rc != 0:
            ausgelassen.append({"path": pfad, "why": "in dieser Revision nicht lesbar"})
            continue
        if len(inhalt.encode()) > MAX_DATEI_BYTES:
            ausgelassen.append({"path": pfad, "why": f"> {MAX_DATEI_BYTES} B — nicht indiziert"})
            continue
        gelesen += 1
        register_pfade = {s.get("path") for s in cfg.registers.values()}
        ist_register = pfad in register_pfade
        for i, zeile in enumerate(inhalt.splitlines(), 1):
            treffer = [m.group(1) if m.groups() else m.group(0) for m in muster.finditer(zeile)]
            if not treffer:
                continue
            hm = heading.match(zeile)
            titel = hm.group(2).strip() if hm else None
            for rid in treffer:
                k = knoten.setdefault(rid, {"defined_at": [], "referenced_at": [], "title": None})
                definition = bool(ist_register and (hm or zeile.lstrip().startswith(f"| {rid} ")))
                ziel = k["defined_at"] if definition else k["referenced_at"]
                if len(ziel) < 40:
                    ziel.append({"path": pfad, "line": i})
                if definition and titel and not k["title"]:
                    k["title"] = titel[:120]
            # Kanten: Ko-Vorkommen in derselben Zeile. Mehr behauptet dieser
            # Index nicht — eine Kante ist ein Hinweis, wo man nachsieht.
            einmalig = sorted(set(treffer))
            behauptet = bool(BEHAUPTUNG.search(zeile))
            for a in range(len(einmalig)):
                for b in range(a + 1, len(einmalig)):
                    if len(kanten) >= 4000:
                        break
                    kanten.append({"from": einmalig[a], "to": einmalig[b],
                                   "path": pfad, "line": i,
                                   "kind": "claim" if behauptet else "co-occurrence",
                                   "why": ("im Text als Beziehung BEHAUPTET — unbelegt"
                                           if behauptet else "in derselben Zeile genannt")})

    return {
        "schema_version": "1.0", "built_by": VERSION, "source": "builtin",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built_for_rev": sha, "project": cfg.project,
        "nodes": knoten, "edges": kanten,
        "coverage": {"files_in_rev": len(pfade), "files_indexed": gelesen,
                     "files_skipped": ausgelassen[:200],
                     "files_skipped_total": len(ausgelassen),
                     "edges_truncated": len(kanten) >= 4000},
        "counts": {"nodes": len(knoten), "edges": len(kanten)},
    }


def veraltet(root: str, index: dict, rev: str) -> dict:
    """Ist der Index noch der geprueften Revision zugeordnet?"""
    sha = voller_sha(root, rev)
    gebaut = index.get("built_for_rev")
    if not sha:
        return {"stale": True, "why": f"Revision {rev!r} nicht aufloesbar", "changed": []}
    if not gebaut:
        return {"stale": True, "why": "der Index nennt keine Revision — unbrauchbar", "changed": []}
    if gebaut == sha:
        unvollstaendig = index.get("coverage", {}).get("files_skipped_total", 0)
        return {"stale": False, "changed": [],
                "why": f"Index gehoert zu {sha[:12]}"
                       + (f"; {unvollstaendig} Datei(en) nicht indiziert" if unvollstaendig else "")}
    rc, diff = git(root, "diff", "--name-only", f"{gebaut}..{sha}")
    geaendert = [x for x in diff.splitlines() if x] if rc == 0 else []
    return {"stale": True, "changed": geaendert[:50], "changed_total": len(geaendert),
            "why": f"Index gehoert zu {gebaut[:12]}, geprueft wird {sha[:12]}"
                   + (f" — {len(geaendert)} Datei(en) dazwischen geaendert" if rc == 0
                      else " — Unterschied nicht bestimmbar")}


def abfragen(index: dict, ids: list[str], depth: int = 1) -> list[dict]:
    treffer: list[dict] = []
    rev = index.get("built_for_rev")
    gesehen: set[tuple] = set()
    front = list(dict.fromkeys(ids))
    for runde in range(max(1, depth)):
        naechste: list[str] = []
        for rid in front:
            k = index.get("nodes", {}).get(rid)
            if not k:
                continue
            for art, orte in (("definition", k["defined_at"]), ("reference", k["referenced_at"])):
                for o in orte:
                    schl = (rid, o["path"], o["line"], art)
                    if schl in gesehen:
                        continue
                    gesehen.add(schl)
                    treffer.append({"id": rid, "path": o["path"], "line": o["line"],
                                    "rev": rev, "kind": art, "distance": runde,
                                    "title": k.get("title"),
                                    "why": "Definition im Register" if art == "definition"
                                           else "Fundstelle im Text"})
            if runde + 1 < max(1, depth):
                for e in index.get("edges", []):
                    if e["from"] == rid:
                        naechste.append(e["to"])
                    elif e["to"] == rid:
                        naechste.append(e["from"])
        front = [x for x in dict.fromkeys(naechste) if x not in ids]
        if not front:
            break
    for e in index.get("edges", []):
        if e.get("kind") == "claim" and (e["from"] in ids or e["to"] in ids):
            schl = ("claim", e["path"], e["line"], e["from"], e["to"])
            if schl in gesehen:
                continue
            gesehen.add(schl)
            treffer.append({"id": e["from"], "related": e["to"], "path": e["path"],
                            "line": e["line"], "rev": rev, "kind": "claim", "distance": 0,
                            "why": "im Text als Beziehung BEHAUPTET — eine Behauptung, "
                                   "kein Nachweis; am Original pruefen"})
    return treffer


# ------------------------------------------------------------ externer Anbieter

def extern_abfragen(cmd: str, root: str, rev: str, ids: list[str], depth: int) -> dict:
    """Externen Anbieter fragen und seine Antwort HART pruefen."""
    anfrage = json.dumps({"op": "query", "rev": rev, "ids": ids, "depth": depth})
    try:
        p = subprocess.run(["bash", "-lc", cmd], cwd=root, input=anfrage,
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": f"Anbieter {cmd!r} antwortete nicht binnen 120 s",
                "hits": [], "rejected": []}
    if p.returncode != 0:
        return {"ok": False, "why": f"Anbieter endete mit Exit {p.returncode}: "
                                    f"{p.stderr.strip()[:200]}", "hits": [], "rejected": []}
    try:
        antwort = json.loads(p.stdout)
    except json.JSONDecodeError as ex:
        return {"ok": False, "why": f"Anbieter lieferte kein JSON: {ex}", "hits": [], "rejected": []}
    if not isinstance(antwort, dict):
        return {"ok": False, "why": "Anbieter lieferte kein Objekt", "hits": [], "rejected": []}

    gut, schlecht = [], []
    for h in (antwort.get("hits") or []):
        if not isinstance(h, dict):
            schlecht.append({"hit": str(h)[:80], "why": "kein Objekt"})
            continue
        fehlt = [f for f in ("id", "path", "line", "rev") if h.get(f) in (None, "")]
        if fehlt:
            schlecht.append({"hit": json.dumps(h, ensure_ascii=False)[:120],
                             "why": f"Pflichtfelder fehlen: {', '.join(fehlt)} — "
                                    "ein Treffer ohne Revision und Fundstelle ist kein Treffer"})
            continue
        if h["rev"] != rev:
            schlecht.append({"hit": json.dumps(h, ensure_ascii=False)[:120],
                             "why": f"Treffer gehoert zu {str(h['rev'])[:12]}, "
                                    f"geprueft wird {rev[:12]}"})
            continue
        gut.append({k: h.get(k) for k in ("id", "path", "line", "rev", "kind", "why",
                                          "title", "related", "distance")})
    return {"ok": True, "source": antwort.get("source") or "extern",
            "stale": bool(antwort.get("stale")),
            "why": antwort.get("why") or "",
            "coverage": antwort.get("coverage") or {},
            "hits": gut, "rejected": schlecht}


def anbieter(root: str, cfg: Config) -> str | None:
    return os.environ.get("DEVOS_GRAPH_CMD") or ((cfg.graph or {}).get("cmd") or None)


def hinweise(root: str, rev_sha: str, ids: list[str], cfg: Config,
             index_pfad: str | None = None, depth: int = 1) -> dict:
    """Der Block, der ins Review-Paket wandert. Nie ein Ersatz, immer ein Zusatz."""
    leer = {"used": False, "source": None, "rev": rev_sha, "stale": None,
            "hits": [], "rejected": [], "why": ""}
    if not ids:
        return {**leer, "why": "keine referenzierten IDs — nichts nachzuschlagen"}

    cmd = anbieter(root, cfg)
    if cmd:
        r = extern_abfragen(cmd, root, rev_sha, ids, depth)
        if not r["ok"]:
            return {**leer, "source": "extern", "why": r["why"] + " — ohne Hinweise weiter"}
        if r["stale"]:
            return {**leer, "source": r["source"], "stale": True,
                    "why": f"der Anbieter meldet seinen Index als veraltet ({r['why']}) — "
                           "ein veralteter Index wird NICHT benutzt",
                    "rejected": r["rejected"]}
        return {"used": True, "source": r["source"], "rev": rev_sha, "stale": False,
                "hits": r["hits"][:200], "rejected": r["rejected"][:20],
                "coverage": r.get("coverage") or {},
                "why": f"{len(r['hits'])} Hinweis(e) vom Anbieter {r['source']!r}"
                       + (f", {len(r['rejected'])} verworfen" if r["rejected"] else "")}

    p = Path(root) / (index_pfad or STANDARD_INDEX)
    if not p.exists():
        return {**leer, "why": f"kein Index unter {index_pfad or STANDARD_INDEX} und kein "
                               "externer Anbieter — das Verfahren laeuft ohne Hinweise weiter"}
    try:
        idx = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        return {**leer, "why": f"Index nicht lesbar: {ex}"}
    zustand = veraltet(root, idx, rev_sha)
    if zustand["stale"]:
        return {**leer, "source": "builtin", "stale": True,
                "why": f"veralteter Index wird NICHT benutzt — {zustand['why']}"}
    t = abfragen(idx, ids, depth)
    return {"used": True, "source": "builtin", "rev": rev_sha, "stale": False,
            "hits": t[:200], "rejected": [],
            "coverage": {k: idx.get("coverage", {}).get(k)
                         for k in ("files_indexed", "files_skipped_total")},
            "why": f"{len(t)} Hinweis(e) aus dem eingebauten Index"
                   + (" (gekuerzt auf 200)" if len(t) > 200 else "")}


def main() -> int:
    ap = argparse.ArgumentParser(description="Kontextindex bauen, abfragen, pruefen")
    ap.add_argument("op", choices=["build", "query", "status"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--index", default=None)
    ap.add_argument("--ids", default="")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = str(Path(a.root).resolve())
    cfg = Config(root)
    ipfad = Path(root) / (a.index or STANDARD_INDEX)

    if a.op == "build":
        idx = bauen(root, a.rev, cfg)
        ipfad.parent.mkdir(parents=True, exist_ok=True)
        ipfad.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        c = idx["coverage"]
        print(f"geschrieben: {ipfad}")
        print(f"Revision {idx['built_for_rev'][:12]} · {idx['counts']['nodes']} IDs · "
              f"{idx['counts']['edges']} Kanten · {c['files_indexed']} von "
              f"{c['files_in_rev']} Dateien indiziert")
        if c["files_skipped_total"]:
            print(f"NICHT indiziert: {c['files_skipped_total']} Datei(en) — "
                  "ein Index, der nicht sagt, was er ausliess, ist der gefaehrlichere")
        return 0

    if a.op == "status":
        if not ipfad.exists():
            print(f"kein Index unter {ipfad}. Das Verfahren laeuft ohne — "
                  "der Index ist eine Erweiterung, keine Voraussetzung.")
            return 1
        idx = json.loads(ipfad.read_text(encoding="utf-8"))
        z = veraltet(root, idx, a.rev)
        cmdname = anbieter(root, cfg)
        if a.json:
            print(json.dumps({**z, "provider": cmdname or "builtin"}, ensure_ascii=False, indent=2))
            return 1 if z["stale"] else 0
        print(f"Index   : {ipfad}")
        print(f"Anbieter: {cmdname or 'builtin'}")
        print(f"Zustand : {'VERALTET' if z['stale'] else 'aktuell'} — {z['why']}")
        for x in z.get("changed", [])[:10]:
            print(f"  geaendert seit dem Indexbau: {x}")
        return 1 if z["stale"] else 0

    sha = voller_sha(root, a.rev)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    h = hinweise(root, sha or a.rev, ids, cfg, a.index, a.depth)
    if a.json:
        print(json.dumps(h, ensure_ascii=False, indent=2))
        return 0
    print(f"Quelle: {h['source'] or '—'} · benutzt: {'ja' if h['used'] else 'nein'}")
    print(f"        {h['why']}")
    for t in h["hits"][:40]:
        mark = "BEHAUPTUNG" if t.get("kind") == "claim" else t.get("kind", "?")
        print(f"  {t['id']:<12} {t['path']}:{t['line']}  [{mark}]  {t.get('why', '')[:60]}")
    for r in h["rejected"][:10]:
        print(f"  VERWORFEN: {r['why']}")
    if h["hits"]:
        print("\nDas sind Zeiger. Geurteilt wird am Original — der Index begrenzt "
              "den Pruefumfang nicht.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
