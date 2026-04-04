#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# sync-folder.sh — Sync video files from source to destination folder.
#
# Copies video files preserving subfolder structure (Season folders).
# If a file exists at destination:
#   - Same modification date  → skip (already up to date)
#   - Different date          → delete + copy (avoids permission errors)
#
# Usage:
#   ./sync-folder.sh "/local/tv/Show" "/remote/tv/Show"
#   ./sync-folder.sh "/local/tv/Show" "/mnt/nas/tv/Show" --dry-run
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DRY_RUN=false
SOURCE=""
DESTINATION=""

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *)
            if [[ -z "$SOURCE" ]]; then
                SOURCE="$arg"
            elif [[ -z "$DESTINATION" ]]; then
                DESTINATION="$arg"
            else
                echo "  [ERROR] Unexpected argument: $arg"; exit 1
            fi
            ;;
    esac
done

if [[ -z "$SOURCE" ]]; then
    echo ""
    echo "  [ERROR] No source folder specified."
    echo ""
    echo "  Usage:"
    echo "    ./sync-folder.sh \"/local/tv/Show\" \"/remote/tv/Show\""
    echo "    ./sync-folder.sh \"/local/Show\" \"/mnt/nas/Show\" --dry-run"
    echo ""
    exit 1
fi

if [[ -z "$DESTINATION" ]]; then
    echo "  [ERROR] No destination folder specified."
    exit 1
fi

if [[ ! -d "$SOURCE" ]]; then
    echo "  [ERROR] Source not found: $SOURCE"
    exit 1
fi

if [[ ! -d "$DESTINATION" ]]; then
    echo "  [ERROR] Destination not found: $DESTINATION"
    exit 1
fi

# Normalize trailing slashes
SOURCE="${SOURCE%/}"
DESTINATION="${DESTINATION%/}"

MODE="LIVE"
$DRY_RUN && MODE="DRY-RUN"

echo ""
echo "  ---------------------------------------------------"
echo "  Sync Folder [$MODE]"
echo "  Source:      $SOURCE"
echo "  Destination: $DESTINATION"
echo "  ---------------------------------------------------"
echo ""

VIDEO_EXTS="mkv|mp4|avi|mov|webm|ogm"

copied=0
skipped=0
replaced=0
errors=0
bytes_copied=0

# Find all video files in source
while IFS= read -r -d '' file; do
    # Build relative path
    rel="${file#$SOURCE/}"
    dest_path="$DESTINATION/$rel"
    dest_dir="$(dirname "$dest_path")"

    # Ensure destination subfolder exists
    if [[ ! -d "$dest_dir" ]]; then
        if $DRY_RUN; then
            echo "  [MKDIR]   $rel"
        else
            mkdir -p "$dest_dir"
        fi
    fi

    # Get source file size in MB
    size_bytes=$(stat -c%s "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null)
    size_mb=$(awk "BEGIN {printf \"%.1f\", $size_bytes / 1048576}")

    if [[ -f "$dest_path" ]]; then
        # Compare modification times (truncated to seconds)
        src_date=$(stat -c%Y "$file" 2>/dev/null || stat -f%m "$file" 2>/dev/null)
        dest_date=$(stat -c%Y "$dest_path" 2>/dev/null || stat -f%m "$dest_path" 2>/dev/null)

        if [[ "$src_date" == "$dest_date" ]]; then
            skipped=$((skipped + 1))
            echo "  [SKIP]    $rel"
            continue
        fi

        # Dates differ — delete + copy
        if $DRY_RUN; then
            src_fmt=$(date -d "@$src_date" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r "$src_date" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
            dest_fmt=$(date -d "@$dest_date" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r "$dest_date" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
            echo "  [REPLACE] $rel ($size_mb MB) src=$src_fmt dest=$dest_fmt"
            replaced=$((replaced + 1))
            continue
        fi

        if rm -f "$dest_path" && cp -p "$file" "$dest_path"; then
            replaced=$((replaced + 1))
            bytes_copied=$((bytes_copied + size_bytes))
            echo "  [REPLACE] $rel ($size_mb MB)"
        else
            errors=$((errors + 1))
            echo "  [ERROR]   $rel"
        fi
    else
        # File doesn't exist at destination
        if $DRY_RUN; then
            echo "  [COPY]    $rel ($size_mb MB)"
            copied=$((copied + 1))
            continue
        fi

        if cp -p "$file" "$dest_path"; then
            copied=$((copied + 1))
            bytes_copied=$((bytes_copied + size_bytes))
            echo "  [COPY]    $rel ($size_mb MB)"
        else
            errors=$((errors + 1))
            echo "  [ERROR]   $rel"
        fi
    fi

done < <(find "$SOURCE" -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.mov' -o -iname '*.webm' -o -iname '*.ogm' \) -print0 | sort -z)

total_gb=$(awk "BEGIN {printf \"%.2f\", $bytes_copied / 1073741824}")

echo ""
echo "  ---------------------------------------------------"
echo "  Skipped:  $skipped (up to date)"
echo "  Copied:   $copied (new)"
echo "  Replaced: $replaced (date mismatch)"
if ! $DRY_RUN; then
    echo "  Transfer: $total_gb GB"
fi
if [[ $errors -gt 0 ]]; then
    echo "  Errors:   $errors"
fi
echo "  ---------------------------------------------------"
echo ""
