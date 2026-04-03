#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# mux_subs.sh — Mux sidecar subtitles into MKV files.
#
# Bash wrapper for mux_subs.py. Scans a folder recursively for MKV files
# that have a .no.srt sidecar and muxes the sidecar into the MKV as an
# embedded Norwegian subtitle track.
#
# Usage:
#   ./mux_subs.sh "/media/tv/Some Show"
#   ./mux_subs.sh "/media/tv/Show" --dry-run
#   ./mux_subs.sh "/media/tv/Show" --keep-sidecar
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_EXE="$PROJECT_DIR/.venv/bin/python"
PYTHON_SCRIPT="$PROJECT_DIR/mux_subs.py"

# ── Validation ───────────────────────────────────────────────────────────────

usage() {
    echo ""
    echo "  [ERROR] $1"
    echo ""
    echo "  Usage:"
    echo "    ./mux_subs.sh \"/media/tv/Some Show\""
    echo "    ./mux_subs.sh \"/media/tv/Show\" --dry-run"
    echo "    ./mux_subs.sh \"/media/tv/Show\" --keep-sidecar"
    echo "    ./mux_subs.sh \"/media/tv/Long Show\" --limit 3"
    echo "    ./mux_subs.sh \"/media/tv/Show\" --log-file /tmp/mux.log"
    echo ""
    exit 1
}

FOLDER=""
PY_ARGS=()

for arg in "$@"; do
    if [[ -z "$FOLDER" ]] && [[ "$arg" != -* ]]; then
        FOLDER="$arg"
    else
        PY_ARGS+=("$arg")
    fi
done

if [[ -z "$FOLDER" ]]; then
    usage "No folder specified."
fi

if [[ ! -d "$FOLDER" ]]; then
    usage "Folder not found: $FOLDER"
fi

FOLDER="$(realpath "$FOLDER")"

if [[ ! -x "$PYTHON_EXE" ]]; then
    if command -v python3 &>/dev/null; then
        PYTHON_EXE="python3"
    else
        usage "Python not found at: $PYTHON_EXE\n         Run install.sh first."
    fi
fi

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    usage "mux_subs.py not found at: $PYTHON_SCRIPT"
fi

# ── Display ──────────────────────────────────────────────────────────────────

echo ""
echo "  ---------------------------------------------------"
echo "  Python:    $PYTHON_EXE"
echo "  Folder:    $FOLDER"
echo "  ---------------------------------------------------"
echo ""

# ── Run ──────────────────────────────────────────────────────────────────────

exec "$PYTHON_EXE" "$PYTHON_SCRIPT" "${PY_ARGS[@]}" "$FOLDER"
