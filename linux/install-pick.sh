#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# install-pick.sh -- Set up the interactive folder picker (pick.sh).
#
# Installs fzf and creates media_roots.conf from the example template.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/dexusno/Translate_Subs/main/linux/install-pick.sh | bash
#   ./install-pick.sh
# ------------------------------------------------------------------------------
set -euo pipefail

echo ""
echo "  ====================================="
echo "    Translate Subs - pick.sh Setup"
echo "  ====================================="
echo ""

# -- Install fzf --------------------------------------------------------------

if command -v fzf >/dev/null 2>&1; then
    echo "  [OK] fzf already installed ($(fzf --version | head -1))"
else
    echo "  Installing fzf..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq fzf > /dev/null 2>&1
    echo "  [OK] fzf installed"
fi

# -- Locate project directory -------------------------------------------------

PROJECT_DIR=""

if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PARENT_DIR="$(dirname "$SCRIPT_DIR")"
    if [[ -f "$PARENT_DIR/translate_subs.py" ]]; then
        PROJECT_DIR="$PARENT_DIR"
    fi
fi

if [[ -z "$PROJECT_DIR" ]]; then
    if [[ -f "$(pwd)/translate_subs.py" ]]; then
        PROJECT_DIR="$(pwd)"
    elif [[ -f "$(pwd)/Translate_Subs/translate_subs.py" ]]; then
        PROJECT_DIR="$(pwd)/Translate_Subs"
    fi
fi

if [[ -z "$PROJECT_DIR" ]]; then
    echo ""
    echo "  [ERROR] Could not find the Translate_Subs project directory."
    echo "          Run this script from inside the project folder, or run"
    echo "          install.sh first to clone the repository."
    exit 1
fi

echo "  Project: $PROJECT_DIR"

# -- Set up media_roots.conf --------------------------------------------------

ROOTS_CONF="$PROJECT_DIR/media_roots.conf"
ROOTS_EXAMPLE="$PROJECT_DIR/media_roots.conf.example"

if [[ -f "$ROOTS_CONF" ]]; then
    echo "  [OK] media_roots.conf already exists (not overwriting)"
elif [[ -f "$ROOTS_EXAMPLE" ]]; then
    cp "$ROOTS_EXAMPLE" "$ROOTS_CONF"
    echo "  [OK] Created media_roots.conf from example"
else
    echo "  [WARNING] media_roots.conf.example not found."
fi

# -- Make sure pick.sh is executable ------------------------------------------

PICK_SH="$PROJECT_DIR/linux/pick.sh"
if [[ -f "$PICK_SH" ]]; then
    chmod +x "$PICK_SH"
fi

# -- Done ---------------------------------------------------------------------

echo ""
echo "  -----------------------------------"
echo "  pick.sh setup complete."
echo ""
echo "  Next steps:"
echo "    1. Edit media_roots.conf with your media library paths:"
echo "       nano $ROOTS_CONF"
echo "    2. Run the picker:"
echo "       $PROJECT_DIR/linux/pick.sh"
echo "  -----------------------------------"
echo ""
