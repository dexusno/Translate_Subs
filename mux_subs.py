"""
mux_subs.py — Mux translated sidecar subtitles into MKV files as embedded tracks.

For each MKV file in a folder, checks if a translated sidecar exists. If found,
muxes it into the MKV with the configured target language tag.
Target language is read from llm_config.json.

Usage:
    python mux_subs.py "D:\\TvSeries\\Some Show"
    python mux_subs.py --dry-run "\\\\nas\\media\\Tv"
    python mux_subs.py --keep-sidecar "D:\\Movies"
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "llm_config.json"

log = logging.getLogger("mux_subs")


def _load_target_language() -> dict:
    """Load target language config from llm_config.json."""
    default = {
        "name": "Norwegian Bokmål",
        "codes": ["no", "nor", "nob", "nb", "nno"],
        "sidecar_code": "no",
        "mkv_tag": "nob",
        "keep_with": ["en", "eng", "da", "dan", "sv", "swe"],
    }
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
        tl = config.get("target_language", default)
        tl["_codes_set"] = set(tl["codes"])
        tl["_keep_languages"] = set(tl["codes"]) | set(tl.get("keep_with", []))
        return tl
    except (FileNotFoundError, json.JSONDecodeError):
        default["_codes_set"] = set(default["codes"])
        default["_keep_languages"] = set(default["codes"]) | set(default["keep_with"])
        return default


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
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def has_target_embedded(streams: list[dict], target_codes: set[str]) -> bool:
    """Check if any subtitle stream already has a target language tag."""
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        lang = (s.get("tags") or {}).get("language", "").lower()
        if lang in target_codes:
            return True
    return False


# ── Mux ───────────────────────────────────────────────────────────────────────


def mux_single_file(
    media: Path,
    srt_path: Path,
    *,
    keep_sidecar: bool = False,
    dry_run: bool = False,
    mkv_tag: str = "nob",
    target_name: str = "Norwegian",
    target_codes: set[str] | None = None,
) -> bool:
    """Mux a sidecar into an MKV as an embedded subtitle track.

    Returns True on success (or skip), False on error.
    Exported for import by translate_subs.py.
    """
    if target_codes is None:
        target_codes = {"no", "nor", "nob", "nb", "nno"}

    if media.suffix.lower() != ".mkv":
        log.info("  [SKIP] Not an MKV: %s", media.name)
        return True

    if not srt_path.exists() or srt_path.stat().st_size == 0:
        log.info("  [SKIP] Sidecar missing or empty: %s", srt_path.name)
        return True

    streams = run_ffprobe(media)
    if not streams:
        log.warning("  [SKIP] No streams found: %s", media.name)
        return False

    if has_target_embedded(streams, target_codes):
        log.info("  [SKIP] %s already embedded: %s", target_name, media.name)
        # Still delete sidecar if not keeping (track is already in MKV)
        if not keep_sidecar and not dry_run and srt_path.exists():
            srt_path.unlink()
            log.info("  Deleted redundant sidecar: %s", srt_path.name)
        return True

    # Count existing subtitle streams — new sub will be at this index
    sub_count = sum(1 for s in streams if s.get("codec_type") == "subtitle")

    if dry_run:
        log.info("  [DRY-RUN] Would mux %s into %s (sub idx %d)",
                 srt_path.name, media.name, sub_count)
        return True

    # Create temp file in same directory (same filesystem for atomic move)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mkv", dir=media.parent, prefix=".tmp_")
    os.close(tmp_fd)
    tmp_file = Path(tmp_path)

    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(media),
            "-i", str(srt_path),
            "-map", "0",
            "-map", "1",
            "-c", "copy",
            f"-metadata:s:s:{sub_count}", f"language={mkv_tag}",
            f"-metadata:s:s:{sub_count}", f"handler_name={target_name} (Translated)",
            str(tmp_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            log.error("  ffmpeg failed: %s", result.stderr[-200:] if result.stderr else "unknown")
            tmp_file.unlink(missing_ok=True)
            return False

        if not tmp_file.exists() or tmp_file.stat().st_size == 0:
            log.error("  ffmpeg produced empty output")
            tmp_file.unlink(missing_ok=True)
            return False

        # Atomic swap: original → backup → swap → delete backup
        backup = media.with_suffix(".mkv.bak")
        try:
            media.rename(backup)
            tmp_file.rename(media)
            backup.unlink()
        except Exception:
            if backup.exists() and not media.exists():
                backup.rename(media)
            tmp_file.unlink(missing_ok=True)
            raise

        # Delete sidecar unless keeping
        if not keep_sidecar:
            srt_path.unlink()
            log.info("  Deleted sidecar: %s", srt_path.name)

        return True

    except Exception as e:
        log.error("  Mux error: %s", e)
        tmp_file.unlink(missing_ok=True)
        return False


# ── Per-file cleaning ─────────────────────────────────────────────────────────

# Lazy-loaded clean_subs functions
_clean_subs_available: bool | None = None
_classify_tracks = None
_remux_without = None
_clean_ffprobe = None


def _ensure_clean_subs_imported() -> bool:
    """Try to import clean_subs functions once."""
    global _clean_subs_available, _classify_tracks, _remux_without, _clean_ffprobe
    if _clean_subs_available is not None:
        return _clean_subs_available
    try:
        from clean_subs import (
            classify_subtitle_tracks,
            remux_without_tracks,
            run_ffprobe as clean_ffprobe,
        )
        _classify_tracks = classify_subtitle_tracks
        _remux_without = remux_without_tracks
        _clean_ffprobe = clean_ffprobe
        _clean_subs_available = True
    except ImportError:
        log.warning("clean_subs.py not found — cleaning disabled")
        _clean_subs_available = False
    return _clean_subs_available


def _clean_single_file(media: Path, rel, stats: dict,
                       keep_languages: set[str] | None = None) -> None:
    """Clean unwanted subtitle tracks from a single MKV."""
    if not _ensure_clean_subs_imported():
        return

    streams = _clean_ffprobe(media)
    if not streams:
        return

    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    if not sub_streams:
        return

    keep, remove = _classify_tracks(media, streams, skip_detect=True,
                                     keep_languages=keep_languages)

    if not remove:
        return

    keep_indices = [s["index"] for s in keep]
    remove_langs = [(s.get("tags") or {}).get("language", "???") for s in remove]

    log.info("[CLEAN] Removing %d track(s): %s — %s",
             len(remove), ", ".join(remove_langs), rel)

    original_size = media.stat().st_size
    if _remux_without(media, keep_indices):
        new_size = media.stat().st_size
        saved_mb = (original_size - new_size) / (1024 * 1024)
        log.info("[CLEAN OK] Removed %d track(s), saved %.1f MB: %s",
                 len(remove), saved_mb, rel)
        stats["cleaned"] = stats.get("cleaned", 0) + 1
        stats["tracks_removed"] = stats.get("tracks_removed", 0) + len(remove)
    else:
        log.error("[CLEAN ERROR] Remux failed: %s", rel)
        stats["clean_errors"] = stats.get("clean_errors", 0) + 1


# ── Batch scan ────────────────────────────────────────────────────────────────


def scan_and_mux(
    folder: Path,
    dry_run: bool = False,
    keep_sidecar: bool = False,
    skip_clean: bool = False,
    limit: int = 0,
    target_lang: dict | None = None,
) -> dict:
    """Scan folder for MKV files, mux in sidecar subtitles, and clean unwanted tracks."""
    if target_lang is None:
        target_lang = _load_target_language()

    sidecar_code = target_lang["sidecar_code"]
    mkv_tag = target_lang["mkv_tag"]
    target_name = target_lang["name"]
    target_codes = target_lang["_codes_set"]
    keep_languages = target_lang["_keep_languages"]
    sidecar_ext = f".{sidecar_code}.srt"

    stats = {"total": 0, "muxed": 0, "skipped": 0, "errors": 0,
             "cleaned": 0, "tracks_removed": 0, "clean_errors": 0}

    mkv_files = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() == ".mkv"
    )
    log.info("Found %d MKV files in %s", len(mkv_files), folder)

    muxed_count = 0

    for media in mkv_files:
        if limit > 0 and muxed_count >= limit:
            log.info("Limit reached (%d files), stopping.", limit)
            break

        stats["total"] += 1
        rel = media.relative_to(folder)

        # Check for sidecar
        srt_path = media.parent / f"{media.stem}{sidecar_ext}"
        if not srt_path.exists() or srt_path.stat().st_size == 0:
            # No sidecar — but still clean if needed
            if not skip_clean and not dry_run:
                _clean_single_file(media, rel, stats, keep_languages)
            else:
                log.info("[SKIP] No %s sidecar: %s", sidecar_ext, rel)
            stats["skipped"] += 1
            continue

        log.info("[MUX] %s", rel)
        ok = mux_single_file(media, srt_path, keep_sidecar=keep_sidecar,
                             dry_run=dry_run, mkv_tag=mkv_tag,
                             target_name=target_name, target_codes=target_codes)
        if ok:
            stats["muxed"] += 1
            # Clean after successful mux
            if not skip_clean and not dry_run:
                try:
                    _clean_single_file(media, rel, stats, keep_languages)
                except Exception as e:
                    log.error("[CLEAN ERROR] %s: %s", rel, e)
                    stats["clean_errors"] += 1
        else:
            stats["errors"] += 1
        muxed_count += 1

    return stats


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    target_lang = _load_target_language()
    sidecar_ext = f".{target_lang['sidecar_code']}.srt"

    parser = argparse.ArgumentParser(
        description=f"Mux {sidecar_ext} sidecar subtitles into MKV files as embedded {target_lang['name']} tracks."
    )
    parser.add_argument("folder", help="Path to folder to scan recursively for MKV files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be muxed without modifying files")
    parser.add_argument("--keep-sidecar", action="store_true",
                        help=f"Keep the {sidecar_ext} sidecar file after muxing (default: delete)")
    parser.add_argument("--skip-clean", action="store_true",
                        help="Skip cleaning unwanted subtitle tracks after muxing")
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

    folder = Path(args.folder)
    if not folder.is_dir():
        log.error("Not a directory: %s", folder)
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    log.info("=== mux_subs [%s] ===", mode)
    log.info("Folder: %s", folder)
    log.info("Target: %s (sidecar: %s, MKV tag: %s)",
             target_lang["name"], sidecar_ext, target_lang["mkv_tag"])
    if args.keep_sidecar:
        log.info("Sidecar: KEEP after mux")
    if args.skip_clean:
        log.info("Clean: OFF")
    if args.log_file:
        log.info("Log file: %s", args.log_file)

    start = time.time()
    stats = scan_and_mux(
        folder, dry_run=args.dry_run,
        keep_sidecar=args.keep_sidecar, skip_clean=args.skip_clean,
        limit=args.limit, target_lang=target_lang,
    )
    elapsed = time.time() - start

    log.info("=" * 60)
    log.info("  Total MKV files:  %d", stats["total"])
    log.info("  Muxed:            %d", stats["muxed"])
    log.info("  Skipped:          %d", stats["skipped"])
    log.info("  Errors:           %d", stats["errors"])
    if not args.skip_clean:
        log.info("  MKVs cleaned:     %d", stats.get("cleaned", 0))
        log.info("  Tracks removed:   %d", stats.get("tracks_removed", 0))
        if stats.get("clean_errors", 0) > 0:
            log.info("  Clean errors:     %d", stats["clean_errors"])
    log.info("  Time:             %.1fs", elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
