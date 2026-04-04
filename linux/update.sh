#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# update.sh -- Update Translate Subs to the latest version.
#
# Pulls the latest changes from GitHub. If local files have been modified,
# stashes them first, applies the update, then restores your changes.
# Reinstalls Python dependencies in case new ones were added.
#
# Usage:
#   ./linux/update.sh
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo ""
echo "  ====================================="
echo "    Translate Subs - Update"
echo "  ====================================="
echo ""

# Check we're in a git repo
if [[ ! -d ".git" ]]; then
    echo "  [ERROR] Not a git repository. Run this from the Translate_Subs folder."
    exit 1
fi

# Check for local changes
has_changes=false
if [[ -n "$(git status --porcelain 2>/dev/null | grep '^\s\?M')" ]]; then
    has_changes=true
fi

if $has_changes; then
    echo "  Local changes detected -- stashing before update..."
    git stash push -m "auto-stash before update" --quiet
    echo "  [OK] Changes stashed"
fi

# Pull latest
echo "  Pulling latest changes..."
if git pull --ff-only 2>&1 | sed 's/^/  /'; then
    echo "  [OK] Updated to latest version"
else
    echo "  [WARNING] Fast-forward pull failed. Trying rebase..."
    if git pull --rebase 2>&1 | sed 's/^/  /'; then
        echo "  [OK] Updated to latest version"
    else
        echo "  [ERROR] Pull failed. You may need to resolve conflicts manually."
        if $has_changes; then
            echo "  Your stashed changes can be restored with: git stash pop"
        fi
        exit 1
    fi
fi

# Restore stashed changes
if $has_changes; then
    echo "  Restoring your local changes..."
    if git stash pop --quiet 2>/dev/null; then
        echo "  [OK] Local changes restored"
    else
        echo "  [WARNING] Could not auto-restore changes. Run 'git stash pop' manually."
        echo "            If there are conflicts, resolve them and run 'git stash drop'."
    fi
fi

# Make sure shell scripts are executable
chmod +x linux/*.sh 2>/dev/null || true

# Reinstall Python dependencies (in case new ones were added)
VENV_PIP="$PROJECT_DIR/.venv/bin/pip"
if [[ -x "$VENV_PIP" ]]; then
    echo "  Updating Python packages..."
    "$VENV_PIP" install --quiet --upgrade requests python-dotenv
    echo "  [OK] Python packages up to date"
fi

# Show current version
latest_tag=$(git describe --tags --abbrev=0 2>/dev/null || echo "unknown")
echo ""
echo "  Version: $latest_tag"
echo ""
echo "  Update complete."
echo ""
