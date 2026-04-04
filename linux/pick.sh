#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pick.sh — Fuzzy-pick a media folder and run a translate_subs action on it.
#
# Usage:
#   ./pick.sh /mnt/media/Tv
#   ./pick.sh /mnt/media/Movies
#   ./pick.sh                        # uses default media root from config
#
# Requires: fzf (sudo apt install fzf)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Default media roots (edit these to match your setup) ─────────────────────
# If no argument is given, these folders are listed together.
DEFAULT_ROOTS=(
    "/mnt/media/Tv"
    "/mnt/media/Movies"
    "/mnt/media/Documentaries"
)

# ── Check fzf ────────────────────────────────────────────────────────────────

if ! command -v fzf &>/dev/null; then
    echo "  fzf not installed. Run: sudo apt install fzf"
    exit 1
fi

# ── Build folder list ────────────────────────────────────────────────────────

MEDIA_ROOT="${1:-}"

if [[ -n "$MEDIA_ROOT" ]]; then
    # Single root provided as argument
    if [[ ! -d "$MEDIA_ROOT" ]]; then
        echo "  [ERROR] Not a directory: $MEDIA_ROOT"
        exit 1
    fi
    ROOTS=("$MEDIA_ROOT")
else
    # Use defaults — filter to ones that actually exist
    ROOTS=()
    for root in "${DEFAULT_ROOTS[@]}"; do
        [[ -d "$root" ]] && ROOTS+=("$root")
    done
    if [[ ${#ROOTS[@]} -eq 0 ]]; then
        echo "  [ERROR] No media roots found. Pass a path or edit DEFAULT_ROOTS in this script."
        exit 1
    fi
fi

# List immediate subdirectories (series/movie folders), prefixed with root
FOLDER_LIST=""
for root in "${ROOTS[@]}"; do
    root_name="$(basename "$root")"
    while IFS= read -r -d '' dir; do
        name="$(basename "$dir")"
        FOLDER_LIST+="[$root_name] $name"$'\n'
    done < <(find "$root" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
done

if [[ -z "$FOLDER_LIST" ]]; then
    echo "  No folders found in: ${ROOTS[*]}"
    exit 1
fi

# ── Pick folder with fzf ────────────────────────────────────────────────────

PICKED=$(echo "$FOLDER_LIST" | fzf \
    --header="Pick a folder (type to filter, Enter to select, Esc to cancel)" \
    --height=40% \
    --reverse \
    --no-mouse \
    --prompt="  > " \
) || { echo "  Cancelled."; exit 0; }

# Parse the selection back to a full path
# Format: "[Tv] Breaking Bad" → find which root contains it
PICKED_ROOT_NAME=$(echo "$PICKED" | sed 's/^\[\([^]]*\)\].*/\1/')
PICKED_FOLDER_NAME=$(echo "$PICKED" | sed 's/^\[[^]]*\] //')

FULL_PATH=""
for root in "${ROOTS[@]}"; do
    if [[ "$(basename "$root")" == "$PICKED_ROOT_NAME" ]]; then
        candidate="$root/$PICKED_FOLDER_NAME"
        if [[ -d "$candidate" ]]; then
            FULL_PATH="$candidate"
            break
        fi
    fi
done

if [[ -z "$FULL_PATH" ]]; then
    echo "  [ERROR] Could not resolve path for: $PICKED"
    exit 1
fi

# ── Pick action ──────────────────────────────────────────────────────────────

ACTION=$(printf '%s\n' \
    "translate        Translate subtitles" \
    "translate-dry    Translate (dry-run preview)" \
    "clean            Clean unwanted subtitle tracks" \
    "clean-dry        Clean (dry-run preview)" \
    "mux              Mux sidecars into MKVs" \
    "mux-dry          Mux (dry-run preview)" \
    "sync             Sync to remote folder" \
    | fzf \
        --header="Action for: $PICKED_FOLDER_NAME" \
        --height=30% \
        --reverse \
        --no-mouse \
        --prompt="  > " \
) || { echo "  Cancelled."; exit 0; }

ACTION_KEY=$(echo "$ACTION" | awk '{print $1}')

# ── Execute ──────────────────────────────────────────────────────────────────

echo ""
echo "  Folder: $FULL_PATH"
echo "  Action: $ACTION_KEY"
echo ""

case "$ACTION_KEY" in
    translate)
        exec "$SCRIPT_DIR/translate_subs.sh" "$FULL_PATH"
        ;;
    translate-dry)
        exec "$SCRIPT_DIR/translate_subs.sh" "$FULL_PATH" --dry-run
        ;;
    clean)
        exec "$SCRIPT_DIR/clean_subs.sh" "$FULL_PATH"
        ;;
    clean-dry)
        exec "$SCRIPT_DIR/clean_subs.sh" "$FULL_PATH" --dry-run
        ;;
    mux)
        exec "$SCRIPT_DIR/mux_subs.sh" "$FULL_PATH"
        ;;
    mux-dry)
        exec "$SCRIPT_DIR/mux_subs.sh" "$FULL_PATH" --dry-run
        ;;
    sync)
        read -rp "  Destination folder: " SYNC_DEST
        if [[ -z "$SYNC_DEST" ]]; then
            echo "  Cancelled."
            exit 0
        fi
        exec "$SCRIPT_DIR/sync-folder.sh" "$FULL_PATH" "$SYNC_DEST"
        ;;
    *)
        echo "  Unknown action: $ACTION_KEY"
        exit 1
        ;;
esac
