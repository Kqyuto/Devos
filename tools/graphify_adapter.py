#!/usr/bin/env python3
"""Graphify (MCP) als Kontextquelle — jeder Treffer wird am Original verifiziert.

    devos graphify probe                      # was kann dieser Server wirklich?
    devos graphify query --rev HEAD --ids D-001,OQ-004
    DEVOS_GRAPH_CMD="python3 $DEVOS_HOME/tools/graphify_adapter.py"   # im Lauf

Konfiguration:

    DEVOS_GRAPHIFY_URL      Standard https://api.graphify.net/mcp
    DEVOS_GRAPHIFY_KEY      Bearer-Schluessel (alternativ GRAPHIFY_API_KEY)
    DEVOS_GRAPHIFY_TOOL     Werkzeugname, falls die Auswahl nicht passt
    DEVOS_GRAPHIFY_ARGS     JSON-Objekt, das in die Argumente gemischt wird
    DEVOS_GRAPHIFY_TIMEOUT  Sekunden, Standard 60

Der Kern dieses Adapters ist NICHT der Aufruf. Er ist die **Verifikation**.

Graphify ist ein Retrieval-Dienst. Was er zurueckgibt, ist Text — vielleicht mit
Pfaden, vielleicht mit Zeilennummern, vielleicht aus einem Index, der drei Wochen
alt ist. DevOS verlangt aber, dass jeder Treffer **Revision und Fundstelle**
nennt, und zwar die der geprueften Lieferung. Beides kann ein externer Dienst gar
nicht wissen.

Deshalb dreht dieser Adapter die Beweislast um:

    Graphify schlaegt vor  →  der Adapter sucht den Vorschlag im Repo
                              bei der GEPRUEFTEN Revision  →  nur was er dort
                              findet, wird zum Treffer, mit exakter Zeile

Das hat drei Folgen, und alle drei sind der Punkt:

  * Ein veralteter Graphify-Index kann nichts durchschmuggeln. Was es im
    geprueften Stand nicht gibt, wird nicht geliefert — es wird als
    *nicht auffindbar* gemeldet.
  * Das Ausgabeformat von Graphify darf sich aendern. Der Adapter sammelt
    Kandidaten grosszuegig; entschieden wird an `git show <rev>:<pfad>`.
  * Ein Treffer ist nie eine Behauptung des Dienstes, sondern eine Stelle im
    Original. Genau das verlangt die Regel *„Originalartefakte bleiben
    massgeblich."*

Exit 0 auch ohne Treffer: kein Kontext ist ein zulaessiges Ergebnis, kein Fehler.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mcp_client import MCPClient, MCPFehler, text_aus   # noqa: E402

STANDARD_URL = "https://api.graphify.net/mcp"
MAX_TREFFER = 120
MAX_KANDIDATEN = 400

# Schluesselnamen, unter denen Retrieval-Dienste ueblicherweise Pfad und Text
# fuehren. Bewusst grosszuegig: was hier durchrutscht, faengt die Verifikation.
PFAD_SCHLUESSEL = ("path", "file", "file_path", "filepath", "filename", "source",
                   "source_path", "uri", "url", "document", "doc", "location")
TEXT_SCHLUESSEL = ("text", "content", "snippet", "chunk", "body", "excerpt", "passage")
ZEILEN_SCHLUESSEL = ("line", "line_number", "lineno", "start_line", "startLine")

FRAGE_SCHLUESSEL = ("query", "q", "question", "text", "prompt", "search", "search_query",
                    "input", "keywords", "term")
GRENZE_SCHLUESSEL = ("limit", "top_k", "topK", "k", "max_results", "maxResults", "n")

# Pfadartige Zeichenketten im Fliesstext. Zwei Formen, und die Trennung hat einen
# Grund: ein Pfad MIT Ordner ist als solcher erkennbar, ein blosser Dateiname wie
# `reg.md` nicht — "z.B." saehe genauso aus. Deshalb zaehlt er nur, wenn er
# ausgezeichnet ist. Was hier trotzdem durchrutscht, faengt die Verifikation;
# was hier zu eng ist, geht als Treffer verloren, und das faengt niemand.
PFAD_MIT_ORDNER = re.compile(r"((?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,6})(?::(\d+))?")
PFAD_AUSGEZEICHNET = re.compile(
    r"[`\"']((?:[\w.\-]+/)*[\w.\-]+\.[A-Za-z0-9]{1,6})(?::(\d+))?[`\"']")


def git(root: str, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def schluessel() -> str | None:
    return os.environ.get("DEVOS_GRAPHIFY_KEY") or os.environ.get("GRAPHIFY_API_KEY")


def verbinden() -> MCPClient:
    url = os.environ.get("DEVOS_GRAPHIFY_URL", STANDARD_URL)
    k = schluessel()
    if not k:
        raise MCPFehler("kein Schluessel — DEVOS_GRAPHIFY_KEY (oder GRAPHIFY_API_KEY) setzen")
    c = MCPClient(url, k, int(os.environ.get("DEVOS_GRAPHIFY_TIMEOUT", "60")))
    c.verbinden()
    return c


# ------------------------------------------------------------- Werkzeugwahl

def werkzeug_waehlen(werkzeuge: list[dict]) -> tuple[dict | None, str]:
    gewuenscht = os.environ.get("DEVOS_GRAPHIFY_TOOL")
    if gewuenscht:
        for t in werkzeuge:
            if t.get("name") == gewuenscht:
                return t, f"ausdruecklich gesetzt (DEVOS_GRAPHIFY_TOOL={gewuenscht})"
        return None, (f"DEVOS_GRAPHIFY_TOOL={gewuenscht!r} gibt es auf diesem Server nicht. "
                      f"Vorhanden: {', '.join(t.get('name', '?') for t in werkzeuge)}")
    punkte = []
    for t in werkzeuge:
        name = (t.get("name") or "").lower()
        beschr = (t.get("description") or "").lower()
        p = 0
        for w, g in (("search", 5), ("query", 5), ("retrieve", 4), ("find", 3),
                     ("lookup", 3), ("context", 3), ("graph", 2), ("ask", 2), ("get", 1)):
            if w in name:
                p += g
            if w in beschr:
                p += 1
        if any(f in (t.get("inputSchema") or {}).get("properties", {}) for f in FRAGE_SCHLUESSEL):
            p += 3
        punkte.append((p, t))
    punkte.sort(key=lambda x: x[0], reverse=True)
    if not punkte or punkte[0][0] <= 0:
        return None, ("kein Werkzeug sieht nach Suche aus. Mit DEVOS_GRAPHIFY_TOOL "
                      "ausdruecklich waehlen. Vorhanden: "
                      + ", ".join(t.get("name", "?") for t in werkzeuge))
    return punkte[0][1], f"aus {len(werkzeuge)} Werkzeug(en) gewaehlt (Punktzahl {punkte[0][0]})"


def argumente_bauen(werkzeug: dict, frage: str) -> tuple[dict, list[str]]:
    schema = werkzeug.get("inputSchema") or {}
    props = schema.get("properties") or {}
    pflicht = list(schema.get("required") or [])
    args: dict = {}
    offen: list[str] = []

    feld = next((f for f in FRAGE_SCHLUESSEL if f in props), None)
    if feld:
        args[feld] = frage
    else:
        strings = [k for k, v in props.items()
                   if (v or {}).get("type") == "string" and k in pflicht]
        if strings:
            args[strings[0]] = frage
        else:
            offen.append("kein erkennbares Frage-Feld im inputSchema")

    grenze = next((f for f in GRENZE_SCHLUESSEL if f in props), None)
    if grenze:
        args[grenze] = 20

    for k in pflicht:
        if k in args:
            continue
        spec = props.get(k) or {}
        if "default" in spec:
            args[k] = spec["default"]
        elif spec.get("enum"):
            args[k] = spec["enum"][0]
        else:
            offen.append(f"Pflichtfeld {k!r} ({spec.get('type', '?')}) kann nicht gefuellt werden")

    try:
        extra = json.loads(os.environ.get("DEVOS_GRAPHIFY_ARGS") or "{}")
        if isinstance(extra, dict):
            args.update(extra)
            offen = [o for o in offen if not any(f"{k!r}" in o for k in extra)]
    except json.JSONDecodeError as ex:
        offen.append(f"DEVOS_GRAPHIFY_ARGS ist kein gueltiges JSON: {ex}")
    return args, offen


# --------------------------------------------------------- Kandidaten sammeln

def kandidaten_sammeln(ergebnis: dict) -> list[dict]:
    """Alles einsammeln, was ein Pfad oder ein Textstueck sein koennte.

    Grosszuegig mit Absicht. Die Strenge steckt in der Verifikation, nicht hier —
    ein Adapter, der schon beim Einsammeln raet, verwirft womoeglich den einen
    brauchbaren Treffer, weil das Ausgabeformat sich geaendert hat.
    """
    gefunden: list[dict] = []

    def aus_dict(d: dict) -> None:
        pfad = next((str(d[k]) for k in PFAD_SCHLUESSEL
                     if isinstance(d.get(k), str) and d.get(k)), None)
        text = next((str(d[k]) for k in TEXT_SCHLUESSEL
                     if isinstance(d.get(k), str) and d.get(k)), None)
        zeile = next((d[k] for k in ZEILEN_SCHLUESSEL if isinstance(d.get(k), int)), None)
        if pfad or text:
            gefunden.append({"path": pfad, "text": text, "line_hint": zeile,
                             "from": "strukturiert"})

    def gehen(x, tiefe: int = 0) -> None:
        if len(gefunden) >= MAX_KANDIDATEN or tiefe > 8:
            return
        if isinstance(x, dict):
            aus_dict(x)
            for v in x.values():
                gehen(v, tiefe + 1)
        elif isinstance(x, list):
            for v in x:
                gehen(v, tiefe + 1)

    if ergebnis.get("structuredContent") is not None:
        gehen(ergebnis["structuredContent"])
    for c in (ergebnis.get("content") or []):
        if isinstance(c, dict):
            gehen(c)

    # Nur die echten Textbloecke nach Pfaden absuchen. Wuerde man die
    # serialisierte `structuredContent`-JSON mitlesen, fande man dort JEDEN Pfad
    # ein zweites Mal — ohne den zugehoerigen Schnipsel, also mit schwaecherem
    # Beleg. Ergebnis waeren Dubletten, bei denen die schlechtere Fundstelle neben
    # der besseren steht. Genau das ist beim ersten Entwurf passiert.
    nur_text = "\n".join(c["text"] for c in (ergebnis.get("content") or [])
                         if isinstance(c, dict) and c.get("type") == "text" and c.get("text"))
    roh = text_aus(ergebnis)
    for muster, woher in ((PFAD_MIT_ORDNER, "Pfad im Fliesstext"),
                          (PFAD_AUSGEZEICHNET, "ausgezeichneter Dateiname im Fliesstext")):
        for m in muster.finditer(nur_text):
            if len(gefunden) >= MAX_KANDIDATEN:
                break
            gefunden.append({"path": m.group(1), "text": None,
                             "line_hint": int(m.group(2)) if m.group(2) else None,
                             "from": woher})
    return gefunden, roh


# ------------------------------------------------------------- Verifikation

def _normal(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def zeile_finden(inhalt: str, ids: list[str], text: str | None,
                 hinweis: int | None) -> tuple[int | None, str, int]:
    """Die belegbare Zeile im ECHTEN Dateiinhalt — oder None mit Grund.

    Der dritte Rueckgabewert ist die BELEGSTUFE: 3 = der Schnipsel des Dienstes
    steht dort wirklich, 2 = wenigstens die ID steht dort, 1 = nur die
    Zeilenangabe des Dienstes liegt im gueltigen Bereich. Damit kann der Aufrufer
    den besseren Beleg vorziehen, statt beide nebeneinander zu fuehren.
    """
    zeilen = inhalt.splitlines()
    if text:
        # Die laengste Zeile des Schnipsels ist die unverwechselbarste — aber die
        # Untergrenze darf nicht so hoch liegen, dass ein kurzer, voellig
        # eindeutiger Schnipsel durchfaellt und der Treffer auf die Ueberschrift
        # zurueckfaellt. Ein zu grob gesetzter Treffer ist schlechter als ein
        # genauer, und beide zeigen ohnehin nur auf eine Stelle im Original.
        bloecke = sorted((z for z in text.splitlines() if len(z.strip()) >= 6),
                         key=lambda z: len(z.strip()), reverse=True)[:5]
        for b in bloecke:
            nb = _normal(b)
            for i, z in enumerate(zeilen, 1):
                if nb and nb in _normal(z):
                    return i, "Schnipsel im geprueften Stand wiedergefunden", 3
    for rid in ids:
        for i, z in enumerate(zeilen, 1):
            if rid in z:
                return i, f"{rid} in dieser Datei gefunden", 2
    if hinweis and 1 <= hinweis <= len(zeilen):
        return hinweis, "Zeilenangabe des Dienstes, im geprueften Stand vorhanden", 1
    return None, ("weder Schnipsel noch eine der IDs noch eine gueltige Zeilenangabe "
                  "im geprueften Stand auffindbar"), 0


def verifizieren(root: str, rev: str, ids: list[str],
                 kandidaten: list[dict]) -> tuple[list[dict], list[dict]]:
    rc, liste = git(root, "ls-tree", "-r", "--name-only", rev)
    if rc != 0:
        return [], [{"why": f"Bestand von {rev[:12]} nicht lesbar — nichts verifizierbar"}]
    bestand = [p for p in liste.splitlines() if p]
    bestand_set = set(bestand)
    cache: dict[str, str] = {}

    treffer: list[dict] = []
    verworfen: list[dict] = []
    gesehen: set[tuple] = set()

    kandidaten = sorted(kandidaten, key=lambda k: (k.get("text") is None,
                                                   k.get("line_hint") is None))
    for k in kandidaten:
        if len(treffer) >= MAX_TREFFER:
            break
        roh = (k.get("path") or "").strip().strip("`\"'").lstrip("./")
        if not roh:
            verworfen.append({"hit": (k.get("text") or "")[:80],
                              "why": "kein Pfad genannt — nicht im Original auffindbar zu machen",
                              "from": k.get("from")})
            continue
        if roh.startswith(("http://", "https://", "file://")):
            roh = roh.split("://", 1)[1].split("/", 1)[-1]
        pfad = (roh if roh in bestand_set else
                next((p for p in bestand if p.endswith("/" + roh)), None) or
                next((p for p in bestand if p == roh), None))
        if not pfad:
            verworfen.append({"hit": roh[:120],
                              "why": f"Pfad existiert in {rev[:12]} nicht — der Index des "
                                     "Dienstes kennt einen Stand, der hier nicht gilt",
                              "from": k.get("from")})
            continue
        if pfad not in cache:
            rc2, inhalt = git(root, "show", f"{rev}:{pfad}")
            cache[pfad] = inhalt if rc2 == 0 else ""
        if not cache[pfad]:
            verworfen.append({"hit": pfad, "why": "in dieser Revision nicht lesbar"})
            continue
        zeile, warum, stufe = zeile_finden(cache[pfad], ids, k.get("text"), k.get("line_hint"))
        if zeile is None:
            verworfen.append({"hit": f"{pfad}", "why": warum, "from": k.get("from")})
            continue
        # Welcher ID gehoert der Treffer? Der, die in der Zeile steht, sonst der ersten.
        zeilentext = cache[pfad].splitlines()[zeile - 1]
        rid = next((x for x in ids if x in zeilentext), ids[0] if ids else "?")
        schl = (rid, pfad, zeile)
        if schl in gesehen:
            continue
        # Fuer dieselbe (ID, Datei) keinen schwaecheren Beleg zusaetzlich fuehren:
        # eine grobe Fundstelle neben einer genauen macht die genaue nicht besser,
        # sondern die Liste unzuverlaessig.
        if any(t["id"] == rid and t["path"] == pfad and t["_stufe"] > stufe for t in treffer):
            continue
        gesehen.add(schl)
        treffer.append({"id": rid, "path": pfad, "line": zeile, "rev": rev,
                        "kind": "graphify", "why": f"Graphify schlug vor; {warum}",
                        "_stufe": stufe})
    for t in treffer:
        t.pop("_stufe", None)
    return treffer, verworfen


# ------------------------------------------------------------------ Abläufe

def probe() -> int:
    try:
        c = verbinden()
    except MCPFehler as ex:
        print(f"Verbindung fehlgeschlagen: {ex}", file=sys.stderr)
        return 1
    print(f"Server   : {c.server.get('name', '?')} {c.server.get('version', '')}")
    print(f"Protokoll: {c.version}" + ("  (mit Sitzungskopf)" if c.session else ""))
    try:
        werkzeuge = c.werkzeuge()
    except MCPFehler as ex:
        print(f"tools/list fehlgeschlagen: {ex}", file=sys.stderr)
        return 1
    print(f"Werkzeuge: {len(werkzeuge)}\n")
    for t in werkzeuge:
        props = (t.get("inputSchema") or {}).get("properties") or {}
        pflicht = set((t.get("inputSchema") or {}).get("required") or [])
        print(f"  {t.get('name')}")
        if t.get("description"):
            print(f"      {t['description'][:150]}")
        for k, v in list(props.items())[:12]:
            print(f"      - {k}{'*' if k in pflicht else ''}: {(v or {}).get('type', '?')}"
                  f"  {str((v or {}).get('description', ''))[:60]}")
        print()
    gew, warum = werkzeug_waehlen(werkzeuge)
    if gew:
        args, offen = argumente_bauen(gew, "D-001 OQ-004")
        print(f"Gewaehlt : {gew.get('name')} — {warum}")
        print(f"Argumente: {json.dumps(args, ensure_ascii=False)}")
        for o in offen:
            print(f"  OFFEN: {o}")
        if offen:
            print("\n  Mit DEVOS_GRAPHIFY_ARGS='{\"feld\": \"wert\"}' ergaenzen oder mit\n"
                  "  DEVOS_GRAPHIFY_TOOL ein anderes Werkzeug waehlen.")
    else:
        print(f"Keine Auswahl moeglich: {warum}")
    print("\n* = Pflichtfeld. Diese Liste kommt vom Server, nicht aus einer Annahme.")
    return 0


def abfrage(root: str, rev: str, ids: list[str], depth: int) -> dict:
    leer = {"source": "graphify", "built_for_rev": rev, "stale": False,
            "hits": [], "coverage": {}}
    if not ids:
        return {**leer, "why": "keine IDs angefragt"}
    try:
        c = verbinden()
        werkzeuge = c.werkzeuge()
    except MCPFehler as ex:
        return {**leer, "why": f"Graphify nicht benutzbar: {ex}"}

    gew, warum = werkzeug_waehlen(werkzeuge)
    if not gew:
        return {**leer, "why": warum}
    frage = " ".join(ids)
    args, offen = argumente_bauen(gew, frage)
    if offen:
        return {**leer, "why": f"Werkzeug {gew.get('name')!r} braucht Argumente, die dieser "
                               f"Adapter nicht fuellen kann: {'; '.join(offen)}"}
    try:
        ergebnis = c.aufrufen(gew["name"], args)
    except MCPFehler as ex:
        return {**leer, "why": f"Aufruf {gew.get('name')!r} fehlgeschlagen: {ex}"}

    kandidaten, roh = kandidaten_sammeln(ergebnis)
    treffer, verworfen = verifizieren(root, rev, ids, kandidaten)
    return {
        "source": f"graphify:{gew.get('name')}",
        "built_for_rev": rev, "stale": False,
        "hits": treffer,
        "rejected": verworfen[:40],
        "coverage": {"tool": gew.get("name"), "tool_choice": warum,
                     "candidates": len(kandidaten), "verified": len(treffer),
                     "unverifiable": len(verworfen), "response_bytes": len(roh.encode())},
        "why": (f"{len(treffer)} von {len(kandidaten)} Vorschlaegen im geprueften Stand "
                f"{rev[:12]} wiedergefunden und mit exakter Zeile belegt"
                + (f"; {len(verworfen)} nicht auffindbar" if verworfen else "")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Graphify (MCP) als verifizierte Kontextquelle")
    ap.add_argument("op", nargs="?", default="stdin", choices=["stdin", "probe", "query"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--ids", default="")
    ap.add_argument("--depth", type=int, default=1)
    a = ap.parse_args()

    if a.op == "probe":
        return probe()

    root = str(Path(a.root).resolve())
    if a.op == "query":
        rc, sha = git(root, "rev-parse", a.rev)
        rev = sha.strip() if rc == 0 else a.rev
        ergebnis = abfrage(root, rev, [x.strip() for x in a.ids.split(",") if x.strip()], a.depth)
        print(json.dumps(ergebnis, ensure_ascii=False, indent=2))
        return 0

    try:
        anfrage = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as ex:
        print(json.dumps({"source": "graphify", "built_for_rev": None, "stale": False,
                          "hits": [], "why": f"Anfrage ist kein JSON: {ex}"}))
        return 0
    rev = anfrage.get("rev") or "HEAD"
    ids = [x for x in (anfrage.get("ids") or []) if isinstance(x, str)]
    print(json.dumps(abfrage(root, rev, ids, int(anfrage.get("depth") or 1)),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
