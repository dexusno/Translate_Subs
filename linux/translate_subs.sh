#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# translate_subs.sh — Translate subtitles in a media folder.
#
# Bash wrapper for translate_subs.py. Scans a folder recursively for video
# files, finds subtitles in any supported source language, and translates
# them using a configurable LLM backend.
#
# Usage:
#   ./translate_subs.sh "/media/tv/Breaking Bad"
#   ./translate_subs.sh "/media/tv/Show" --dry-run
#   ./translate_subs.sh "/media/movies" --profile openai --batch-size 200
#   ./translate_subs.sh "/media/tv/Show" --limit 5 --parallel 4
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_EXE="$PROJECT_DIR/.venv/bin/python"
PYTHON_SCRIPT="$PROJECT_DIR/translate_subs.py"

# ── Validation ───────────────────────────────────────────────────────────────

usage() {
    echo ""
    echo "  [ERROR] $1"
    echo ""
    echo "  Usage:"
    echo "    ./translate_subs.sh \"/media/tv/Breaking Bad\""
    echo "    ./translate_subs.sh \"/media/movies\" --dry-run"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --batch-size 200 --parallel 2"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --limit 3"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --force"
    echo "    ./translate_subs.sh \"/media/movies\" --profile openai --dry-run"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --skip-clean"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --skip-detect"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --keep-sidecar"
    echo "    ./translate_subs.sh \"/media/tv/Show\" --log-file /tmp/translate.log"
    echo ""
    exit 1
}

# Extract folder (first non-flag argument)
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
    # Fallback to system python
    if command -v python3 &>/dev/null; then
        PYTHON_EXE="python3"
    else
        usage "Python not found at: $PYTHON_EXE\n         Run install.sh first or edit PYTHON_EXE in this script."
    fi
fi

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    usage "translate_subs.py not found at: $PYTHON_SCRIPT"
fi

# Warn if .env missing
ENV_FILE="$PROJECT_DIR/.env"
IS_DRY_RUN=false
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && IS_DRY_RUN=true
done

if [[ ! -f "$ENV_FILE" ]] && ! $IS_DRY_RUN; then
    echo "  [WARNING] .env not found at: $ENV_FILE"
    echo "            API key must be set as an environment variable."
    echo ""
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
