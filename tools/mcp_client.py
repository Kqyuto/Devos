"""Minimaler MCP-Client ueber Streamable HTTP — stdlib only.

Genug fuer den Zweck: verbinden, Werkzeuge auflisten, eines aufrufen. Kein
vollstaendiger MCP-Client, und diese Datei sagt das, statt es offenzulassen.

Warum nicht das offizielle SDK: dieselbe Regel wie ueberall in DevOS — ein Gate,
das von einem Installationsschritt abhaengt, wird in der Praxis optional. Ein
Kontextabruf, der `pip install` braucht, faellt beim ersten Rechnerwechsel aus,
und dann laeuft das Verfahren ohne ihn weiter, ohne dass jemand es merkt.

Unterstuetzt:
  * JSON-RPC ueber POST, Antwort als `application/json` ODER `text/event-stream`
  * Sitzungskopf `Mcp-Session-Id`, wenn der Server einen vergibt
  * Protokollversion aushandeln, mit Rueckfall auf aeltere Fassungen
  * Bearer-Authentisierung

NICHT unterstuetzt (und deshalb hier benannt): OAuth-Flows, serverseitig
angestossene Anfragen, Ressourcen, Prompts, Abmeldung, Wiederaufnahme per
`Last-Event-ID`. Ein Server, der eines davon verlangt, wird eine klare Fehlmeldung
bekommen und keine stille Teilfunktion.

Der Schluessel wird nirgends protokolliert und in keiner Fehlmeldung wiederholt.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

VERSIONEN = ["2025-06-18", "2025-03-26", "2024-11-05"]
CLIENT = {"name": "devos", "version": "1.0"}


class MCPFehler(Exception):
    """Ein benannter Fehler. Nie mit Schluesselinhalt."""


class MCPClient:
    def __init__(self, url: str, token: str | None = None, timeout: int = 60):
        self.url = url
        self._token = token
        self.timeout = timeout
        self.session: str | None = None
        self.version: str | None = None
        self.server: dict = {}
        self._id = 0

    # ------------------------------------------------------------ Transport
    def _kopf(self) -> dict:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        return h

    def _senden(self, nachricht: dict, erwartet_antwort: bool = True) -> dict | None:
        daten = json.dumps(nachricht).encode("utf-8")
        req = urllib.request.Request(self.url, data=daten, headers=self._kopf())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    self.session = sid
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                rumpf = r.read().decode("utf-8", "replace")
                status = r.status
        except urllib.error.HTTPError as ex:
            rumpf = ex.read().decode("utf-8", "replace")[:400]
            if ex.code in (401, 403):
                auth = ex.headers.get("WWW-Authenticate")
                raise MCPFehler(
                    f"HTTP {ex.code} — der Schluessel wird nicht akzeptiert"
                    + (f"; der Server verlangt {auth!r} (OAuth wird von diesem Client "
                       "NICHT unterstuetzt)" if auth else "")
                    + f". Antwort: {rumpf[:200]}")
            raise MCPFehler(f"HTTP {ex.code}: {rumpf[:250]}")
        except Exception as ex:
            raise MCPFehler(f"Transportfehler: {type(ex).__name__}: {str(ex)[:200]}")

        if not erwartet_antwort:
            return None
        if status == 202 or not rumpf.strip():
            return None
        if ctype == "text/event-stream":
            return self._aus_sse(rumpf, nachricht.get("id"))
        try:
            antwort = json.loads(rumpf)
        except json.JSONDecodeError as ex:
            raise MCPFehler(f"Antwort ist kein JSON: {ex}; Anfang: {rumpf[:150]!r}")
        if isinstance(antwort, list):          # Stapelantwort
            for a in antwort:
                if isinstance(a, dict) and a.get("id") == nachricht.get("id"):
                    return a
            raise MCPFehler("Stapelantwort ohne passende id")
        return antwort

    @staticmethod
    def _aus_sse(rumpf: str, wunsch_id: Any) -> dict:
        """Die Antwort mit der passenden id aus dem Ereignisstrom ziehen."""
        letzter = None
        for block in rumpf.split("\n\n"):
            zeilen = [z for z in block.splitlines() if z.startswith("data:")]
            if not zeilen:
                continue
            roh = "\n".join(z[5:].lstrip() for z in zeilen)
            try:
                obj = json.loads(roh)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                if obj.get("id") == wunsch_id:
                    return obj
                letzter = obj
        if letzter is not None:
            return letzter
        raise MCPFehler("Ereignisstrom enthielt keine verwertbare JSON-RPC-Antwort")

    def _aufruf(self, methode: str, params: dict | None = None) -> dict:
        self._id += 1
        antwort = self._senden({"jsonrpc": "2.0", "id": self._id, "method": methode,
                                "params": params or {}})
        if antwort is None:
            raise MCPFehler(f"{methode}: keine Antwort erhalten")
        if "error" in antwort:
            e = antwort["error"]
            raise MCPFehler(f"{methode}: [{e.get('code')}] {str(e.get('message'))[:200]}")
        return antwort.get("result") or {}

    # -------------------------------------------------------------- Ablauf
    def verbinden(self) -> dict:
        """initialize + notifications/initialized. Handelt die Version aus."""
        letzter = None
        for v in VERSIONEN:
            self.version = None          # beim ersten Versuch keinen Kopf senden
            try:
                r = self._aufruf("initialize", {
                    "protocolVersion": v, "capabilities": {}, "clientInfo": CLIENT})
            except MCPFehler as ex:
                letzter = ex
                if "Schluessel" in str(ex) or "Transportfehler" in str(ex):
                    raise            # Authentisierung/Netz: weitere Versuche sind sinnlos
                continue
            self.version = r.get("protocolVersion") or v
            self.server = r.get("serverInfo") or {}
            self.capabilities = r.get("capabilities") or {}
            try:
                self._senden({"jsonrpc": "2.0", "method": "notifications/initialized"},
                             erwartet_antwort=False)
            except MCPFehler:
                pass                  # manche Server antworten hier gar nicht
            return {"protocolVersion": self.version, "serverInfo": self.server,
                    "capabilities": self.capabilities, "session": bool(self.session)}
        raise MCPFehler(f"initialize scheiterte fuer alle Versionen {VERSIONEN}: {letzter}")

    def werkzeuge(self) -> list[dict]:
        alle, cursor = [], None
        for _ in range(20):                      # begrenzt: keine Endlosschleife
            p = {"cursor": cursor} if cursor else {}
            r = self._aufruf("tools/list", p)
            alle += [t for t in (r.get("tools") or []) if isinstance(t, dict)]
            cursor = r.get("nextCursor")
            if not cursor:
                break
        return alle

    def aufrufen(self, name: str, argumente: dict) -> dict:
        r = self._aufruf("tools/call", {"name": name, "arguments": argumente})
        if r.get("isError"):
            texte = [c.get("text", "") for c in (r.get("content") or [])
                     if isinstance(c, dict)]
            raise MCPFehler(f"Werkzeug {name!r} meldet Fehler: {' '.join(texte)[:250]}")
        return r


def text_aus(ergebnis: dict) -> str:
    """Alle Textbausteine eines tools/call-Ergebnisses aneinander."""
    teile = []
    for c in (ergebnis.get("content") or []):
        if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
            teile.append(c["text"])
    if ergebnis.get("structuredContent") is not None:
        teile.append(json.dumps(ergebnis["structuredContent"], ensure_ascii=False))
    return "\n".join(teile)
