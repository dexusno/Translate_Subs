"""
clean_subs.py — Remove unwanted subtitle tracks from MKV files.

Keeps only the target language (from llm_config.json) and any additional
languages listed in keep_with. For tracks with no language tag, optionally
identifies the language via LLM API.

Usage:
    python clean_subs.py "D:\\TvSeries\\Some Show"
    python clean_subs.py --dry-run "\\\\nas\\media\\Tv"
    python clean_subs.py --skip-detect "D:\\Movies"
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "llm_config.json"


def _load_keep_languages() -> set[str]:
    """Build KEEP_LANGUAGES from llm_config.json target_language settings."""
    fallback = {
        "no", "nor", "nob", "nb", "nno",
        "en", "eng", "da", "dan", "sv", "swe",
    }
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
        tl = config.get("target_language", {})
        codes = set(tl.get("codes", []))
        keep_with = set(tl.get("keep_with", []))
        if codes:
            return codes | keep_with
        return fallback
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


# Languages to keep — built from config, everything else gets removed
KEEP_LANGUAGES = _load_keep_languages()

# Text subtitle codecs (can extract sample text for language detection)
TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}

API_KEY = os.getenv("DEEPSEEK_API_KEY")
API_URL = "https://api.deepseek.com/v1/chat/completions"

log = logging.getLogger("clean_subs")

# ── ffprobe ───────────────────────────────────────────────────────────────────


def run_ffprobe(path: Path) -> list[dict]:
    """Return all stream metadata from a media file."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,codec_name:stream_tags=language,title",
                "-of", "json",
                str(path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


# ── Language detection ────────────────────────────────────────────────────────


def _extract_text_sample(media: Path, stream_index: int, max_cues: int = 10) -> str:
    """Extract a small text sample from a subtitle track."""
    tmp = Path(tempfile.mktemp(suffix=".srt"))
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-nostdin",
                "-i", str(media),
                "-map", f"0:{stream_index}",
                "-c:s", "text",
                str(tmp),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode != 0 or not tmp.exists():
            return ""

        text = tmp.read_text(encoding="utf-8-sig", errors="replace")
        # Extract just the dialogue lines (skip index numbers and timestamps)
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.match(r"^\d+$", line):
                continue
            if re.search(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->", line):
                continue
            lines.append(line)
            if len(lines) >= max_cues * 2:
                break
        return "\n".join(lines)
    finally:
        if tmp.exists():
            tmp.unlink()


def identify_language(text_sample: str) -> str | None:
    """Ask DeepSeek to identify the language of a text sample.

    Returns an ISO 639-1 code (e.g. 'en', 'no', 'es') or None on failure.
    """
    if not API_KEY or not text_sample.strip():
        return None

    try:
        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a language detection tool. "
                            "Identify the language of the given text. "
                            "Reply with ONLY the ISO 639-1 two-letter language code "
                            "(e.g. en, no, da, sv, es, fr, de, hr, pt). "
                            "Nothing else."
                        ),
                    },
                    {"role": "user", "content": text_sample[:500]},
                ],
                "temperature": 0.0,
            },
            timeout=15,
        )
        resp.raise_for_status()
        code = resp.json()["choices"][0]["message"]["content"].strip().lower()
        # Validate: should be 2-3 letter code
        if re.match(r"^[a-z]{2,3}$", code):
            return code
        return None
    except Exception:
        return None


# ── Track classification ─────────────────────────────────────────────────────


def classify_subtitle_tracks(
    media: Path,
    streams: list[dict],
    skip_detect: bool = False,
    keep_languages: set[str] | None = None,
    remove_bitmap: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Classify subtitle streams into keep and remove lists.

    Args:
        keep_languages: Override the default KEEP_LANGUAGES set.
            When called from translate_subs.py, this is built from config.
        remove_bitmap: If True, always remove bitmap/PGS subtitle tracks
            regardless of language. Configurable via remove_bitmap_subs
            in llm_config.json.

    Returns (keep, remove) where each is a list of stream dicts.
    """
    langs_to_keep = keep_languages if keep_languages is not None else KEEP_LANGUAGES

    keep = []
    remove = []

    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    # Bitmap subtitle codecs (PGS, DVD subs)
    bitmap_codecs = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}

    for s in sub_streams:
        idx = s["index"]
        codec = s.get("codec_name", "?")
        tags = s.get("tags") or {}
        lang = tags.get("language", "").strip().lower()
        title = tags.get("title", "")

        # Remove bitmap/PGS tracks when configured (default: yes)
        if remove_bitmap and codec in bitmap_codecs:
            log.debug("  [REMOVE] idx=%d %s lang=%s (bitmap, incompatible) %s",
                      idx, codec, lang or "???", title)
            remove.append(s)
            continue

        if lang and lang in langs_to_keep:
            log.debug("  [KEEP]   idx=%d %s lang=%s %s", idx, codec, lang, title)
            keep.append(s)
            continue

        if lang and lang not in langs_to_keep:
            log.debug("  [REMOVE] idx=%d %s lang=%s %s", idx, codec, lang, title)
            remove.append(s)
            continue

        # Undefined language tag
        if skip_detect:
            log.debug("  [KEEP]   idx=%d %s lang=??? (skip-detect) %s", idx, codec, title)
            keep.append(s)
            continue

        # Try to detect language via text extraction + API
        if codec in TEXT_SUB_CODECS:
            log.debug("  [DETECT] idx=%d %s — extracting sample...", idx, codec)
            sample = _extract_text_sample(media, idx)
            if sample:
                detected = identify_language(sample)
                if detected:
                    if detected in langs_to_keep:
                        log.debug("  [KEEP]   idx=%d detected=%s", idx, detected)
                        keep.append(s)
                    else:
                        log.debug("  [REMOVE] idx=%d detected=%s", idx, detected)
                        remove.append(s)
                    continue

            # Detection failed — keep to be safe
            log.debug("  [KEEP]   idx=%d %s — detection failed, keeping", idx, codec)
            keep.append(s)
        else:
            # Unknown codec with no language — keep to be safe
            log.debug("  [KEEP]   idx=%d %s lang=??? (unknown codec) %s", idx, codec, title)
            keep.append(s)

    return keep, remove


# ── Remux ─────────────────────────────────────────────────────────────────────


def remux_without_tracks(media: Path, keep_sub_indices: list[int]) -> bool:
    """Remux MKV keeping only specified subtitle tracks.

    Uses ffmpeg -c copy (no re-encoding). Writes to temp file, then replaces original.
    Returns True on success.
    """
    # Create temp file in same directory (same filesystem for atomic move)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mkv", dir=media.parent, prefix=".tmp_")
    os.close(tmp_fd)
    tmp_file = Path(tmp_path)

    try:
        # Build map args: all video + audio, then specific subtitle tracks
        map_args = ["-map", "0:v", "-map", "0:a"]
        for idx in sorted(keep_sub_indices):
            map_args.extend(["-map", f"0:{idx}"])

        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", str(media),
            "-c", "copy",
            *map_args,
            str(tmp_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)

        if result.returncode != 0:
            log.error("  ffmpeg failed: %s", result.stderr[-200:] if result.stderr else "unknown")
            tmp_file.unlink(missing_ok=True)
            return False

        if not tmp_file.exists() or tmp_file.stat().st_size == 0:
            log.error("  ffmpeg produced empty output")
            tmp_file.unlink(missing_ok=True)
            return False

        # Atomic-ish replace: original → backup → swap → delete backup
        backup = media.with_suffix(".mkv.bak")
        try:
            media.rename(backup)
            tmp_file.rename(media)
            backup.unlink()
            return True
        except Exception:
            # Restore original if swap fails
            if backup.exists() and not media.exists():
                backup.rename(media)
            tmp_file.unlink(missing_ok=True)
            raise

    except Exception as e:
        log.error("  Remux error: %s", e)
        tmp_file.unlink(missing_ok=True)
        return False


# ── Main scan + clean ─────────────────────────────────────────────────────────


def scan_and_clean(
    folder: Path,
    dry_run: bool = False,
    skip_detect: bool = False,
    limit: int = 0,
    log_file: str = "",
) -> dict:
    """Scan folder for MKV files and remove unwanted subtitle tracks."""
    stats = {"total": 0, "cleaned": 0, "skipped": 0, "errors": 0, "tracks_removed": 0}

    mkv_files = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() == ".mkv"
    )

    log.info("Found %d MKV files in %s", len(mkv_files), folder)

    cleaned_count = 0

    for media in mkv_files:
        if limit > 0 and cleaned_count >= limit:
            log.info("Limit reached (%d files), stopping.", limit)
            break

        stats["total"] += 1
        rel = media.relative_to(folder)

        log.info("[SCAN] %s", rel)
        streams = run_ffprobe(media)
        if not streams:
            log.info("  No streams found, skipping")
            stats["skipped"] += 1
            continue

        sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
        if not sub_streams:
            log.info("  No subtitle tracks")
            stats["skipped"] += 1
            continue

        keep, remove = classify_subtitle_tracks(media, streams, skip_detect=skip_detect)

        if not remove:
            log.info("  Nothing to remove")
            stats["skipped"] += 1
            continue

        keep_indices = [s["index"] for s in keep]
        remove_langs = [
            (s.get("tags") or {}).get("language", "???") for s in remove
        ]

        if dry_run:
            log.info("  [DRY-RUN] Would remove %d track(s): %s",
                     len(remove), ", ".join(remove_langs))
            stats["cleaned"] += 1
            stats["tracks_removed"] += len(remove)
            cleaned_count += 1
            continue

        log.info("  Removing %d track(s): %s — remuxing...",
                 len(remove), ", ".join(remove_langs))

        original_size = media.stat().st_size
        if remux_without_tracks(media, keep_indices):
            new_size = media.stat().st_size
            saved_mb = (original_size - new_size) / (1024 * 1024)
            log.info("  [OK] Removed %d track(s), saved %.1f MB", len(remove), saved_mb)
            stats["cleaned"] += 1
            stats["tracks_removed"] += len(remove)
        else:
            log.error("  [ERROR] Remux failed: %s", rel)
            stats["errors"] += 1

        cleaned_count += 1

    return stats


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    # Show which languages will be kept
    keep_display = ", ".join(sorted(KEEP_LANGUAGES))
    parser = argparse.ArgumentParser(
        description="Remove unwanted subtitle tracks from MKV files. "
                    f"Keeps: {keep_display}"
    )
    parser.add_argument("folder", help="Path to folder to scan recursively for MKV files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be removed without modifying files")
    parser.add_argument("--skip-detect", action="store_true",
                        help="Keep tracks with undefined language instead of detecting via API")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of files to process (0 = unlimited)")
    parser.add_argument("--log-file", type=str, default="",
                        help="Also write log output to this file")
    args = parser.parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    if not args.skip_detect and not API_KEY and not args.dry_run:
        log.warning("DEEPSEEK_API_KEY not set — undefined language tracks will be kept.")
        log.warning("Use --skip-detect to suppress this warning.")

    folder = Path(args.folder)
    if not folder.is_dir():
        log.error("Not a directory: %s", folder)
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    log.info("=== clean_subs [%s] ===", mode)
    log.info("Folder: %s", folder)
    if args.skip_detect:
        log.info("Language detection: OFF (keeping undefined tracks)")
    if args.log_file:
        log.info("Log file: %s", args.log_file)

    start = time.time()
    stats = scan_and_clean(
        folder, dry_run=args.dry_run, skip_detect=args.skip_detect,
        limit=args.limit,
    )
    elapsed = time.time() - start

    log.info("=" * 60)
    log.info("  Total MKV files:  %d", stats["total"])
    log.info("  Cleaned:          %d", stats["cleaned"])
    log.info("  Skipped:          %d", stats["skipped"])
    log.info("  Errors:           %d", stats["errors"])
    log.info("  Tracks removed:   %d", stats["tracks_removed"])
    log.info("  Time:             %.1fs", elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
