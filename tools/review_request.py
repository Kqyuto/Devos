#!/usr/bin/env python3
"""Erzeugt das Review-Paket aus einer Lieferung — deterministisch, ohne Modell.

Der Punkt ist NICHT Bequemlichkeit. Er ist, dass der Reviewer nicht sieht, was ein
Mensch ausgewaehlt hat, sondern was die Lieferung tatsaechlich ist. Vier harte Regeln:

  1. Der Diff geht VOLLSTAENDIG mit. Reisst eine Datei das Groessenlimit, wird sie
     nicht gekuerzt, sondern wandert nach `omitted` — mit Grund.
  2. `omitted` ist Pflichtbestandteil, auch leer. Ein Paket, das nicht sagt, was es
     weggelassen hat, ist genau die Zusammenfassungsebene, auf der Verzerrung entsteht.
  3. Tests laufen in einem isolierten Worktree des GEPRUEFTEN Stands, nie im
     Arbeitsverzeichnis. Sonst kann lokaler Reparaturcode einen aelteren fehlerhaften
     Commit gruen testen.                                              (Befund F03)
  4. Nichts wird still gekuerzt. Das vollstaendige Testprotokoll liegt als Artefakt
     daneben; jede Kuerzung im Markdown nennt Umfang, Grund und Fundort. (Befund F04)

stdlib only. Eine Abhaengigkeit, die man installieren muss, ist ein Gate, das in der
Praxis optional wird.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_config import Config  # noqa: E402

MAX_FILE_BYTES = 120_000
MAX_TOTAL_BYTES = 900_000
MD_LOG_BYTES = 6_000


def sh(*args: str, cwd: str | None = None) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"fehlgeschlagen: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def changed_files(root: str, base: str, head: str) -> list[dict]:
    """numstat NUL-separiert — Umbenennungen liefern zwei Pfade, nicht 'a => b'. (F04)"""
    raw = subprocess.run(["git", "-C", root, "diff", "--numstat", "-z", f"{base}..{head}"],
                         capture_output=True, text=True, check=True).stdout
    toks = raw.split("\0")
    rows, i = [], 0
    while i < len(toks):
        t = toks[i]
        if not t:
            i += 1
            continue
        parts = t.split("\t")
        if len(parts) < 3:
            i += 1
            continue
        added, removed, tail = parts[0], parts[1], parts[2]
        if tail == "":                      # Umbenennung: die naechsten zwei Token
            old, new = toks[i + 1], toks[i + 2]
            i += 3
            rows.append({"path": new, "renamed_from": old, "added": None if added == "-" else int(added),
                         "removed": None if removed == "-" else int(removed), "binary": added == "-"})
        else:
            i += 1
            rows.append({"path": tail, "renamed_from": None, "added": None if added == "-" else int(added),
                         "removed": None if removed == "-" else int(removed), "binary": added == "-"})
    return rows


def file_diff(root: str, base: str, head: str, f: dict) -> str:
    paths = [f["path"]] + ([f["renamed_from"]] if f["renamed_from"] else [])
    return sh("git", "-C", root, "diff", f"{base}..{head}", "--", *paths)


def read_task(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    task = {"id": path.stem, "file": str(path), "raw": text}
    m = re.search(r"^#\s*(\S+)\s*[—-]?\s*(.*)$", text, re.M)
    if m:
        task["id"], task["title"] = m.group(1), m.group(2).strip()
    for key, label in (("acceptance", "Acceptance"), ("forbidden", "Forbidden"),
                       ("decisions", "Relevante Beschluesse")):
        sec = re.search(rf"^##\s*{label}\s*$\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        task[key] = _bullets(sec.group(1)) if sec else []
    return task


def _bullets(block: str) -> list[str]:
    """Aufzaehlungspunkte mit angehaengten Fortsetzungszeilen.

    Ein Acceptance-Punkt ueber zwei Zeilen darf den Reviewer nicht halbiert
    erreichen — genau diese stille Kuerzung hat im ersten Lauf zugeschlagen.
    """
    items: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("-", "*")):
            items.append(s[1:].strip())
        elif items:
            items[-1] += " " + s
    return items


# 12 KB, weil D-198 (Zustimmungsform-Regel, 8,9 KB) genau die Passage ist, die der
# Reviewer der Runde 1 ausdruecklich verlangt hat. Ein Limit, das die angefragte
# Norm ausschliesst, verfehlt seinen Zweck.
MAX_NORM_BYTES = 12_000


def norm_sources(root: str, ids: list[str], cfg: Config) -> tuple[dict, list[dict]]:
    """Zieht die Normquelle je referenzierter ID mit.

    Der Reviewer der Runde 1 konnte die Governance-Konformitaet nicht pruefen, weil
    das Paket nur IDs nannte und keine Wortlaute. Eine ID ohne ihren Text ist genau
    die Art Zeiger, die das Projekt selbst als 'toten Anker' fuehrt.
    """
    found, missing = {}, []
    cache: dict[str, list[str]] = {}
    hinweis = cfg.hinweis()
    if hinweis:
        return {}, [hinweis]
    for rid in ids:
        pre = rid.split("-")[0]
        spec = cfg.registers.get(pre)
        if not spec:
            missing.append({"path": rid, "why": f"kein Register fuer Praefix {pre!r} in .devos.json"})
            continue
        rel, kind = spec["path"], spec.get("kind", "row")
        f = Path(root) / rel
        if not f.exists():
            missing.append({"path": rid, "why": f"{rel} nicht vorhanden"})
            continue
        lines = cache.setdefault(rel, f.read_text(encoding="utf-8").splitlines())
        text = None
        if kind == "heading":
            naechste = spec.get("heading_pattern", rf"^#{{1,4}}\s+{re.escape(pre)}-\d+\b")
            for i, l in enumerate(lines):
                if re.match(rf"^#{{1,4}}\s+{re.escape(rid)}\b", l):
                    j = i + 1
                    while j < len(lines) and not re.match(naechste, lines[j]):
                        j += 1
                    text = "\n".join(lines[i:j]).rstrip()
                    break
        else:
            hits = [l for l in lines if l.lstrip().startswith(f"| {rid} ")]
            if hits:
                text = "\n".join(hits[-2:]) if len(hits) > 1 else hits[0]
        if text is None:
            missing.append({"path": rid, "why": f"in {rel} nicht auffindbar — toter Anker"})
            continue
        n = len(text.encode("utf-8"))
        if n > MAX_NORM_BYTES:
            missing.append({"path": rid, "why": f"Normtext {n} B > Limit {MAX_NORM_BYTES} B; "
                                               f"Fundstelle {rel}", "bytes": n})
            continue
        found[rid] = {"source": rel, "text": text}
    return found, missing


def run_tests_isolated(root: str, head: str, cmd: str | None, out: Path) -> dict:
    """Tests im Worktree des geprueften Stands. Nie im Arbeitsverzeichnis. (F03)"""
    dirty = bool(sh("git", "-C", root, "status", "--porcelain").strip())
    if not cmd:
        return {"ran": False, "isolated": False, "worktree_dirty": dirty,
                "why": "kein Testkommando uebergeben (--tests) — das ist ein Befund, kein neutraler Zustand",
                "exit_code": None, "passed": False}

    wt = Path(tempfile.mkdtemp(prefix="devos-wt-"))
    try:
        r = subprocess.run(["git", "-C", root, "worktree", "add", "--detach", str(wt), head],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return {"ran": False, "isolated": False, "worktree_dirty": dirty,
                    "why": f"isolierter Worktree nicht erstellbar: {r.stderr.strip()[:300]}",
                    "exit_code": None, "passed": False}
        p = subprocess.run(["bash", "-lc", cmd], cwd=str(wt), capture_output=True, text=True)
        full = p.stdout + p.stderr
        log = out / "test-output.txt"
        log.write_text(full, encoding="utf-8")
        return {"ran": True, "isolated": True, "ran_against": head, "worktree_dirty": dirty,
                "command": cmd, "exit_code": p.returncode, "passed": p.returncode == 0,
                "output_bytes": len(full.encode()), "output_sha256": hashlib.sha256(full.encode()).hexdigest()[:16],
                "output_file": str(log.relative_to(Path(root))), "output_tail": full[-MD_LOG_BYTES:],
                "output_truncated_in_markdown": len(full.encode()) > MD_LOG_BYTES}
    finally:
        subprocess.run(["git", "-C", root, "worktree", "remove", "--force", str(wt)],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", root, "worktree", "prune"], capture_output=True, text=True)
        shutil.rmtree(wt, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Review-Paket einer Lieferung erzeugen")
    ap.add_argument("--task", required=True)
    ap.add_argument("--base", default=None)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--onto", default=None, help="Zielbranch fuer die merge-base")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="devos/review")
    ap.add_argument("--tests", default=None)
    a = ap.parse_args()

    root = str(Path(a.root).resolve())
    cfg = Config(root)
    task = read_task(Path(a.task))

    # Base und Head IMMER zu vollen SHAs aufloesen — Praefixe binden nichts. (F01)
    if a.base:
        base = sh("git", "-C", root, "rev-parse", a.base).strip()
    elif a.onto:
        base = sh("git", "-C", root, "merge-base", a.onto, a.head).strip()
    else:
        base = sh("git", "-C", root, "rev-parse", f"{a.head}^").strip()
    head = sh("git", "-C", root, "rev-parse", a.head).strip()

    out = Path(root) / a.out
    out.mkdir(parents=True, exist_ok=True)

    files = changed_files(root, base, head)
    diffs, omitted, total = {}, [], 0
    selbst = str(Path(a.out).as_posix()).strip("/") + "/"
    for f in files:
        # Das Ausgabeverzeichnis aus dem eigenen Diff nehmen: sonst prueft jedes
        # Paket die Pakete aller Vorrunden mit, und der Umfang waechst quadratisch.
        # Sichtbar gemacht, nicht stillschweigend - das ist der ganze Punkt von `omitted`.
        if f["path"].startswith(selbst):
            omitted.append({"path": f["path"], "why": "Ausgabeverzeichnis des Review-Werkzeugs — "
                            "ein Paket prueft nicht die Pakete der Vorrunden mit"})
            continue
        if f["binary"]:
            omitted.append({"path": f["path"], "why": "binaer — kein Textdiff"})
            continue
        d = file_diff(root, base, head, f)
        n = len(d.encode("utf-8"))
        if n == 0:
            omitted.append({"path": f["path"], "why": "git meldete die Datei als geaendert, "
                            "lieferte aber einen leeren Diff — ungeklaert, nicht als enthalten gefuehrt",
                            "renamed_from": f["renamed_from"]})
            continue
        if n > MAX_FILE_BYTES:
            omitted.append({"path": f["path"], "why": f"Einzeldiff {n} B > Limit {MAX_FILE_BYTES} B",
                            "bytes": n, "sha256": hashlib.sha256(d.encode()).hexdigest()[:16]})
            continue
        if total + n > MAX_TOTAL_BYTES:
            omitted.append({"path": f["path"], "why": f"Gesamtlimit {MAX_TOTAL_BYTES} B erreicht", "bytes": n})
            continue
        diffs[f["path"]] = d
        total += n

    tests = run_tests_isolated(root, head, a.tests, out)
    if tests.get("output_truncated_in_markdown"):
        omitted.append({"path": tests["output_file"],
                        "why": f"Testprotokoll {tests['output_bytes']} B — im Markdown nur die letzten "
                               f"{MD_LOG_BYTES} B; das vollstaendige Protokoll liegt als Artefakt daneben",
                        "sha256": tests["output_sha256"]})

    id_re = re.compile(cfg.id_pattern)
    refs = sorted({m if isinstance(m, str) else m[0]
                   for m in id_re.findall(task["raw"] + "\n" + "\n".join(diffs.values()))})
    norms, norms_missing = norm_sources(root, refs, cfg)
    omitted += norms_missing
    commits = sh("git", "-C", root, "log", "--format=%h %s", f"{base}..{head}").splitlines()

    ctx = {
        "schema_version": "1.1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "devos/tools/review_request.py@1.1",
        "project": cfg.project,
        "task": {k: task.get(k) for k in ("id", "title", "file", "acceptance", "forbidden", "decisions")},
        "range": {"base": base, "head": head, "commits": commits},
        "files_changed": files, "diff_included": sorted(diffs), "omitted": omitted,
        "referenced_ids": refs, "norm_sources": norms, "tests": tests,
        "counts": {"files": len(files), "diff_bytes": total, "omitted": len(omitted), "commits": len(commits)},
    }
    (out / "review_context.json").write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")

    L = [f"# REVIEW REQUEST — {ctx['task']['id']}", ""]
    if ctx["task"].get("title"):
        L.append(f"**{ctx['task']['title']}**\n")
    L += [f"Erzeugt: `{ctx['generated_at']}`", "",
          f"- **base** `{base}`", f"- **head** `{head}`",
          f"- {len(files)} Dateien · {len(commits)} Commits · Diff {total} B", "",
          "> Du bist **Adversarial Reviewer**. Du gibst diesen Gegenstand **nicht** frei —",
          "> das tut der Mensch. Du lieferst ein `review_result.json` nach",
          "> `devos/schema/review_result.schema.json` zurueck. **`reviewed_range.base` und",
          "> `.head` muessen die vollen SHAs oben sein — das Gate vergleicht exakt.**",
          "> **Reicht dieses Paket nicht zum Urteilen, ist die richtige Antwort",
          "> `INSUFFICIENT_CONTEXT` und nicht `PASS`.**", ""]

    L += ["## Acceptance (aus der Task)", ""]
    L += [f"- {x}" for x in (ctx["task"]["acceptance"] or ["— keine angegeben —"])]
    L += ["", "## Forbidden", ""]
    L += [f"- {x}" for x in (ctx["task"]["forbidden"] or ["— keine angegeben —"])]

    L += ["", "## Tests", ""]
    if tests["ran"]:
        L += [f"Kommando: `{tests['command']}`", "",
              f"- gelaufen gegen **`{tests['ran_against']}`** in einem **isolierten Worktree** "
              f"(Arbeitsverzeichnis war {'schmutzig' if tests['worktree_dirty'] else 'sauber'}, spielt keine Rolle)",
              f"- Exit `{tests['exit_code']}` — **{'bestanden' if tests['passed'] else 'FEHLGESCHLAGEN'}**",
              f"- Protokoll {tests['output_bytes']} B, sha256 `{tests['output_sha256']}`, "
              f"vollstaendig in `{tests['output_file']}`", ""]
        if tests["output_truncated_in_markdown"]:
            L += [f"> **Gekuerzt:** unten stehen die letzten {MD_LOG_BYTES} von "
                  f"{tests['output_bytes']} B. Der Rest steht in `{tests['output_file']}`.", ""]
        L += ["```", tests["output_tail"].strip(), "```"]
    else:
        L += [f"**Nicht ausgefuehrt** — {tests['why']}"]

    L += ["", "## Referenzierte Register-IDs", "", (", ".join(f"`{r}`" for r in refs) or "— keine —")]
    if norms:
        L += ["", "### Normquellen im Wortlaut", "",
              "*Damit die Governance-Konformitaet ohne Nachfrage pruefbar ist. "
              "Eine ID ohne ihren Text ist ein toter Anker.*", ""]
        for rid in sorted(norms):
            L += [f"<details><summary><code>{rid}</code> — <code>{norms[rid]['source']}</code></summary>",
                  "", "```", norms[rid]["text"], "```", "</details>", ""]
    L += ["", "## Geaenderte Dateien", "", "| Datei | + | − | Umbenannt von |", "|---|---|---|---|"]
    L += ["| `{}` | {} | {} | {} |".format(
        f["path"], f["added"] if f["added"] is not None else "—",
        f["removed"] if f["removed"] is not None else "—",
        f"`{f['renamed_from']}`" if f["renamed_from"] else "—") for f in files]

    L += ["", "## Nicht mitgeschickt", ""]
    if omitted:
        L += ["**Der Reviewer sieht folgendes NICHT:**", ""]
        L += [f"- `{o['path']}` — {o['why']}" for o in omitted]
        L += ["", "Ein Urteil, das von einem dieser Posten abhaengt, ist `INSUFFICIENT_CONTEXT`."]
    else:
        L += ["Nichts. Der Diff ist vollstaendig und das Testprotokoll ungekuerzt."]

    L += ["", "## Diff", ""]
    for p in sorted(diffs):
        L += [f"### `{p}`", "", "```diff", diffs[p].rstrip(), "```", ""]

    (out / "REVIEW-REQUEST.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"geschrieben: {out/'REVIEW-REQUEST.md'}\ngeschrieben: {out/'review_context.json'}")
    if tests["ran"]:
        print(f"geschrieben: {out/'test-output.txt'}")
    print(f"base {base[:12]} · head {head[:12]} · Dateien {len(files)} · Diff {total} B · "
          f"nicht mitgeschickt {len(omitted)} · Tests "
          f"{'bestanden (isoliert)' if tests.get('passed') else ('FEHLGESCHLAGEN' if tests['ran'] else 'nicht gelaufen')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
