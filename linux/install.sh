#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# install.sh — Install and configure Translate Subs on Debian 13
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dexusno/Translate_Subs/main/linux/install.sh | bash
#   ./install.sh
#   ./install.sh --python /usr/bin/python3.12
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/dexusno/Translate_Subs.git"
PYTHON_EXE=""
INSTALL_DIR=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)  PYTHON_EXE="$2"; shift 2 ;;
        --dir)     INSTALL_DIR="$2"; shift 2 ;;
        *)         echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo "  ====================================="
echo "    Translate Subs - Install (Debian)"
echo "  ====================================="
echo ""

# ── System packages ──────────────────────────────────────────────────────────

echo "  Installing system dependencies..."

sudo apt-get update -qq

sudo apt-get install -y -qq \
    python3 \
    python3-pip \
    python3-venv \
    ffmpeg \
    git \
    mkvtoolnix \
    > /dev/null 2>&1

echo "  [OK] System packages installed"

# ── Determine project directory ──────────────────────────────────────────────

SCRIPT_DIR=""
NEEDS_CLONE=true

# If run from a file, check if we're already in the repo
if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # We're in the linux/ subfolder — check parent for translate_subs.py
    PARENT_DIR="$(dirname "$SCRIPT_DIR")"
    if [[ -f "$PARENT_DIR/translate_subs.py" ]]; then
        NEEDS_CLONE=false
        SCRIPT_DIR="$PARENT_DIR"
    fi
fi

if $NEEDS_CLONE; then
    if [[ -z "$INSTALL_DIR" ]]; then
        INSTALL_DIR="$(pwd)/Translate_Subs"
    fi

    if [[ -f "$INSTALL_DIR/translate_subs.py" ]]; then
        echo "  [OK] Repository already cloned at: $INSTALL_DIR"
    else
        echo "  Cloning repository to: $INSTALL_DIR"
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
        echo "  [OK] Repository cloned"
    fi
    SCRIPT_DIR="$INSTALL_DIR"
fi

echo "  Project: $SCRIPT_DIR"

# ── Find Python 3.11+ ───────────────────────────────────────────────────────

if [[ -z "$PYTHON_EXE" ]]; then
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" &>/dev/null; then
            ver=$("$candidate" --version 2>&1 | grep -oP '\d+\.\d+')
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 11 ]]; then
                PYTHON_EXE="$candidate"
                break
            fi
        fi
    done
fi

if [[ -z "$PYTHON_EXE" ]]; then
    echo "  [ERROR] Python 3.11+ not found."
    echo "          Install with: sudo apt-get install python3.11"
    echo "          Or specify: ./install.sh --python /path/to/python3"
    exit 1
fi

PY_VERSION=$("$PYTHON_EXE" --version 2>&1)
echo "  [OK] Python: $PYTHON_EXE ($PY_VERSION)"

# ── Check ffmpeg ─────────────────────────────────────────────────────────────

if command -v ffmpeg &>/dev/null && command -v ffprobe &>/dev/null; then
    echo "  [OK] ffmpeg and ffprobe found on PATH"
else
    echo "  [WARNING] ffmpeg/ffprobe not found on PATH."
    echo "            Install: sudo apt-get install ffmpeg"
fi

# ── Create virtual environment ───────────────────────────────────────────────

VENV_DIR="$SCRIPT_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
    echo "  [OK] Virtual environment already exists"
else
    echo "  Creating virtual environment..."
    "$PYTHON_EXE" -m venv "$VENV_DIR"
    echo "  [OK] Virtual environment created at: $VENV_DIR"
fi

# ── Install Python dependencies ──────────────────────────────────────────────

echo "  Installing Python packages..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet --upgrade requests python-dotenv
echo "  [OK] requests, python-dotenv installed"

# ── Set up .env ──────────────────────────────────────────────────────────────

ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [[ -f "$ENV_FILE" ]]; then
    echo "  [OK] .env already exists (not overwriting)"
elif [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "  [OK] Created .env from .env.example"
    echo ""
    echo "  !! Edit .env and add your API key for your chosen provider."
else
    echo "  [WARNING] .env.example not found - create .env manually."
fi

# ── Set up llm_config.json ───────────────────────────────────────────────────

LLM_CONFIG="$SCRIPT_DIR/llm_config.json"
LLM_EXAMPLE="$SCRIPT_DIR/llm_config.example.json"

if [[ -f "$LLM_CONFIG" ]]; then
    echo "  [OK] llm_config.json already exists (not overwriting)"
elif [[ -f "$LLM_EXAMPLE" ]]; then
    cp "$LLM_EXAMPLE" "$LLM_CONFIG"
    echo "  [OK] Created llm_config.json from example"
    echo "     Edit llm_config.json to configure target language and profiles."
else
    echo "  [WARNING] llm_config.example.json not found."
fi

# ── Make bash wrappers executable ────────────────────────────────────────────

LINUX_DIR="$SCRIPT_DIR/linux"
if [[ -d "$LINUX_DIR" ]]; then
    chmod +x "$LINUX_DIR"/*.sh 2>/dev/null || true
    echo "  [OK] Bash wrappers made executable"
fi

# ── Update bash wrappers with detected Python path ──────────────────────────

VENV_PYTHON="$VENV_DIR/bin/python"
for wrapper in "$LINUX_DIR"/translate_subs.sh "$LINUX_DIR"/mux_subs.sh "$LINUX_DIR"/clean_subs.sh; do
    if [[ -f "$wrapper" ]]; then
        sed -i "s|^PYTHON_EXE=.*|PYTHON_EXE=\"$VENV_PYTHON\"|" "$wrapper"
    fi
done
echo "  [OK] Bash wrappers configured with venv Python"

# ── Verify ───────────────────────────────────────────────────────────────────

echo ""
echo "  -----------------------------------"

CONFIG_FILE="$SCRIPT_DIR/llm_config.json"
if [[ -f "$CONFIG_FILE" ]]; then
    TARGET=$("$VENV_PYTHON" -c "import json; c=json.load(open('$CONFIG_FILE')); print(c['target_language']['name'])" 2>/dev/null || echo "?")
    DEFAULT=$("$VENV_PYTHON" -c "import json; c=json.load(open('$CONFIG_FILE')); print(c['default_profile'])" 2>/dev/null || echo "?")
    PROFILES=$("$VENV_PYTHON" -c "import json; c=json.load(open('$CONFIG_FILE')); print(', '.join(c['profiles'].keys()))" 2>/dev/null || echo "?")
    echo "  Target:    $TARGET"
    echo "  Profiles:  $PROFILES"
    echo "  Default:   $DEFAULT"
fi

echo "  -----------------------------------"
echo ""
echo "  Install complete."
echo ""
echo "  Next steps:"
echo "    1. cd $SCRIPT_DIR"
echo "    2. Edit .env and add your API key"
echo "    3. Test with:"
echo "       ./linux/translate_subs.sh \"/media/tv/Some Show\" --dry-run"
echo ""
