#!/usr/bin/env bash
# Builder-Adapter: Claude Code als Builder in einem DevOS-Lauf.
#
#   DEVOS_BUILDER_CMD="bash $DEVOS_HOME/tools/builders/claude_code.sh"
#
# Der Orchestrator uebergibt ueber die Umgebung:
#   DEVOS_BRIEF        Pfad zum BUILD-BRIEF.md dieser Runde
#   DEVOS_ROUND        Rundennummer
#   DEVOS_HEAD_BEFORE  Commit vor dem Bau
#   DEVOS_TASK_FILE    die Taskdatei
#   DEVOS_RUN_DIR      Laufverzeichnis
# Gearbeitet wird im Projektwurzelverzeichnis (cwd setzt der Orchestrator).
#
# Vertrag, den run_task.py danach prueft — und den dieses Skript deshalb einhaelt:
#   * es MUSS ein neuer Commit entstehen
#   * das Arbeitsverzeichnis MUSS danach sauber sein
# Beides nicht aus Ordnungsliebe: Tests und Urteil werden an eine Revision
# gebunden, und ein schmutziges Arbeitsverzeichnis gehoert zu keiner.

set -euo pipefail

: "${DEVOS_BRIEF:?DEVOS_BRIEF ist nicht gesetzt — dieses Skript gehoert an DEVOS_BUILDER_CMD}"
[ -f "$DEVOS_BRIEF" ] || { echo "Brief nicht gefunden: $DEVOS_BRIEF" >&2; exit 1; }

BIN="${DEVOS_BUILDER_BIN:-claude}"
command -v "$BIN" >/dev/null 2>&1 || {
  echo "[$BIN] nicht im PATH. Entweder Claude Code installieren oder DEVOS_BUILDER_CMD" >&2
  echo "auf ein anderes Builder-Kommando setzen." >&2
  exit 1
}

# acceptEdits laesst Dateiaenderungen zu, ohne jede Shell-Zeile freizugeben.
# Wer den Builder auch Tests laufen lassen will, setzt das hier bewusst hoeher —
# und weiss dann, dass er es getan hat.
MODE="${DEVOS_BUILDER_PERMISSION_MODE:-acceptEdits}"

ARGS=(-p --permission-mode "$MODE")
[ -n "${DEVOS_BUILDER_MODEL:-}" ] && ARGS+=(--model "$DEVOS_BUILDER_MODEL")
[ -n "${DEVOS_BUILDER_EXTRA_ARGS:-}" ] && read -r -a EXTRA <<< "$DEVOS_BUILDER_EXTRA_ARGS" && ARGS+=("${EXTRA[@]}")

echo "  [claude_code.sh] Runde ${DEVOS_ROUND:-?} · Modus $MODE · Brief $(basename "$DEVOS_BRIEF")"

{
  echo "Du bist BUILDER in einem DevOS-Lauf. Arbeite den folgenden Auftrag ab."
  echo "Aendere NUR, was der Auftrag verlangt. Committe deine Arbeit am Ende selbst"
  echo "mit einer aussagekraeftigen Nachricht und lass das Arbeitsverzeichnis sauber."
  echo "Ein Reviewer einer anderen Modellfamilie prueft anschliessend genau diesen Commit."
  echo
  cat "$DEVOS_BRIEF"
} | "$BIN" "${ARGS[@]}" || {
  echo "  [claude_code.sh] Builder endete mit Fehler" >&2
  exit 1
}

# Hat er selbst committet? Wenn nicht, aber etwas geaendert: wir committen —
# und sagen im Commit-Text, dass der Builder es nicht getan hat. Stillschweigend
# nachbessern waere das Gegenteil dessen, wofuer dieses Verfahren gebaut ist.
if [ -n "$(git status --porcelain)" ]; then
  echo "  [claude_code.sh] Der Builder hat nicht committet — wird nachgeholt und vermerkt."
  git add -A
  git commit -q -m "Builder-Lieferung Runde ${DEVOS_ROUND:-?} (nachtraeglich committet)

Der Builder hat seine Aenderungen nicht selbst committet. Dieser Commit wurde
vom Adapter erzeugt, damit die Lieferung an eine Revision gebunden werden kann.
Die Commit-Nachricht stammt deshalb NICHT vom Builder.

Auftrag: ${DEVOS_BRIEF}"
fi

if [ "$(git rev-parse HEAD)" = "${DEVOS_HEAD_BEFORE:-}" ]; then
  echo "  [claude_code.sh] Kein neuer Commit — es gibt nichts zu pruefen." >&2
  exit 2
fi
echo "  [claude_code.sh] Lieferung: $(git rev-parse --short HEAD)"
