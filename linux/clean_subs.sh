#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# clean_subs.sh — Remove unwanted subtitle tracks from MKV files.
#
# Bash wrapper for clean_subs.py. Scans a folder recursively for MKV files
# and removes subtitle tracks that are not Norwegian, English, Danish,
# or Swedish.
#
# Usage:
#   ./clean_subs.sh "/media/tv/Some Show"
#   ./clean_subs.sh "/media/tv/Show" --dry-run
#   ./clean_subs.sh "/media/tv/Big Lib" --limit 10 --skip-detect
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_EXE="$PROJECT_DIR/.venv/bin/python"
PYTHON_SCRIPT="$PROJECT_DIR/clean_subs.py"

# ── Validation ───────────────────────────────────────────────────────────────

usage() {
    echo ""
    echo "  [ERROR] $1"
    echo ""
    echo "  Usage:"
    echo "    ./clean_subs.sh \"/media/tv/Some Show\""
    echo "    ./clean_subs.sh \"/media/tv/Show\" --dry-run"
    echo "    ./clean_subs.sh \"/media/tv/Big Lib\" --limit 10"
    echo "    ./clean_subs.sh \"/media/tv/Show\" --skip-detect"
    echo "    ./clean_subs.sh \"/media/tv/Show\" --log-file /tmp/clean.log"
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
    usage "clean_subs.py not found at: $PYTHON_SCRIPT"
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
