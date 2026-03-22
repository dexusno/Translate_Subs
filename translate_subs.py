"""
translate_subs.py — Scan a media folder, find subtitles in any supported
source language, translate them to a configurable target language via LLM.

Target language, source languages, and LLM provider are configured in
llm_config.json next to this script.

Usage:
    python translate_subs.py "D:\\Movies\\Some Movie"
    python translate_subs.py --batch-size 500 --parallel 3 --dry-run "/mnt/media/Tv/Ugly Betty"
    python translate_subs.py --profile openai "D:\\TvSeries\\Breaking Bad"
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# Force UTF-8 output on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".webm", ".ogm", ".avi"}

# Text subtitle codecs ffmpeg can convert to SRT
TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
# Bitmap subtitle codecs (need OCR, not supported)
BITMAP_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}

# HI/SDH patterns in track title or language tag
HI_PATTERNS = {"sdh", "hearing", "hearing-impaired", "hearing impaired",
                "hi", "cc", "closed captions", "closed-captions", "captions"}

log = logging.getLogger("translate_subs")

# ── LLM config loading ───────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "llm_config.json"


def load_config() -> dict:
    """Load llm_config.json from next to this script."""
    if not CONFIG_FILE.exists():
        log.error("Config file not found: %s", CONFIG_FILE)
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def resolve_profile(config: dict, profile_name: str | None) -> dict:
    """Resolve a named profile to {api_url, model, api_key}."""
    name = profile_name or config.get("default_profile", "deepseek")
    profiles = config.get("profiles", {})
    if name not in profiles:
        log.error("Unknown profile '%s'. Available: %s", name, ", ".join(profiles))
        sys.exit(1)
    p = profiles[name]
    # Resolve api_key: from env var or literal
    if "api_key_env" in p:
        api_key = os.getenv(p["api_key_env"], "")
    else:
        api_key = p.get("api_key", "")
    return {
        "name": name,
        "api_url": p["api_url"],
        "model": p["model"],
        "api_key": api_key,
    }


def get_source_languages(config: dict) -> list[dict]:
    """Return the ordered source language priority list from config."""
    return config.get("source_languages", [
        {"codes": ["en", "eng"], "name": "English"},
    ])


def get_target_language(config: dict) -> dict:
    """Return the target language config, with derived sets for convenience."""
    default = {
        "name": "Norwegian Bokmål",
        "codes": ["no", "nor", "nob", "nb", "nno"],
        "sidecar_code": "no",
        "mkv_tag": "nob",
        "keep_with": ["en", "eng", "da", "dan", "sv", "swe"],
    }
    tl = config.get("target_language", default)
    # Build derived sets
    tl["_codes_set"] = set(tl["codes"])
    tl["_keep_languages"] = set(tl["codes"]) | set(tl.get("keep_with", []))
    return tl


# ══════════════════════════════════════════════════════════════════════════════
# SRT Utilities (inlined from app/srtxlate.py)
# ══════════════════════════════════════════════════════════════════════════════


def _nfc(s: str) -> str:
    """Normalize string to NFC form."""
    return unicodedata.normalize("NFC", s or "")


def _strip_bom(line: str) -> str:
    return line.lstrip("\ufeff") if line else line


_TAG_RE = re.compile(r"<[^>]+>")


def _protect_tags(text: str) -> Tuple[str, Dict[str, str]]:
    """Replace HTML-ish tags with placeholders to protect from translation."""
    tags: Dict[str, str] = {}

    def _replace(m):
        key = f"__TAG{len(tags)}__"
        tags[key] = m.group(0)
        return key

    protected_text = _TAG_RE.sub(_replace, text)
    return protected_text, tags


def _restore_tags(text: str, tags: Dict[str, str]) -> str:
    """Restore placeholders back to original tags."""
    for key, val in tags.items():
        text = text.replace(key, val)
    return text


_TIME_RE = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}")


def _is_index_line(line: str) -> bool:
    return line.strip().isdigit()


def _is_time_line(line: str) -> bool:
    return bool(_TIME_RE.search(line or ""))


_ALLCAPS_RE = re.compile(r"^[^a-zåäöæøéèáíóúñçß]+$")


def _is_allcaps_marker(line: str) -> bool:
    """Treat short ALL-CAPS cues like [DOOR OPENS], (MUSIC) as markers."""
    txt = _TAG_RE.sub("", line or "").strip()
    if len(txt) == 0:
        return False
    if len(txt) <= 40 and _ALLCAPS_RE.match(txt) and any(ch.isalpha() for ch in txt):
        return True
    return False


def _split_srt(s: str) -> List[List[str]]:
    """Split SRT file content into a list of blocks (each block is a list of lines)."""
    if not s:
        return []
    normalized = s.replace("\r\n", "\n").replace("\r", "\n")
    chunks = re.split(r"\n{2,}", normalized)
    blocks: List[List[str]] = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        lines = chunk.split("\n")
        if not blocks and lines:
            lines[0] = _strip_bom(lines[0])
        blocks.append(lines)
    return blocks


def _join_srt(blocks: List[List[str]]) -> str:
    """Join blocks of lines back into a single SRT string, with trailing newline."""
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def _split_to_n_lines_preserving_words(
    text: str, n: int, target_lengths: Optional[List[int]] = None
) -> List[str]:
    """Split text into exactly n lines, preferring spaces near proportional cut points."""
    text = " ".join((text or "").replace("\n", " ").split())
    if n <= 1:
        return [text]

    parts = [p.strip() for p in re.split(r"\r?\n", text) if p.strip()]
    if len(parts) == n:
        return parts

    total_len = max(1, len(text))
    if not target_lengths or len(target_lengths) != n:
        target_lengths = [round(total_len / n)] * n

    out: List[str] = []
    start = 0
    for i in range(n):
        remaining = text[start:].lstrip()
        if i == n - 1:
            out.append(remaining)
            break

        cut_target = min(len(remaining), max(1, target_lengths[i]))
        best = None
        r = remaining.find(" ", cut_target)
        if r != -1:
            best = r
        if best is None:
            l = remaining.rfind(" ", 0, cut_target)
            if l != -1:
                best = l
        if best is None:
            best = cut_target

        left = remaining[:best].rstrip()
        out.append(left)
        start += len(remaining[:best])
        if start < len(text) and text[start] == " ":
            start += 1

    while len(out) < n:
        out.append("")
    return out[:n]


# ══════════════════════════════════════════════════════════════════════════════
# Translation Engine (inlined from test_deepseek.py, generalized)
# ══════════════════════════════════════════════════════════════════════════════

_SENTINEL = "__NL__"


def _build_system_prompt(source_lang: str, target_name: str) -> str:
    """Build the translation system prompt for the given source and target languages."""
    return (
        f"You are a professional subtitle translator. "
        f"Translate the following {source_lang} subtitle lines to {target_name}. "
        f"Each line is prefixed with [N] where N is the line number. "
        f"Return each translated line prefixed with the SAME [N] marker. "
        f"Preserve any __NL__ markers exactly as they appear — do not translate or remove them. "
        f"Preserve any __TAG0__, __TAG1__ etc. placeholders exactly. "
        f"Keep sound effects in ALL CAPS (e.g. ENGINE ROARS → MOTOREN BRØLER). "
        f"Do not add any explanations, only the numbered translated lines."
    )


def _llm_translate_batched(
    lines: List[str],
    source_lang: str,
    batch_size: int = 30,
    glossary: Optional[Dict[str, str]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    *,
    api_url: str,
    model: str,
    api_key: str,
    target_name: str = "Norwegian Bokmål",
) -> List[str]:
    """Translate lines via an OpenAI-compatible LLM API."""
    if not lines:
        return []

    glossary = glossary or {}
    system_prompt = _build_system_prompt(source_lang, target_name)

    # Protect tags + glossary substitutions
    protected_pairs = []
    prepped: List[str] = []
    for ln in lines:
        ln0, tags = _protect_tags(ln)
        gtext = ln0
        for k, v in glossary.items():
            gtext = re.sub(rf"\b{re.escape(k)}\b", v, gtext, flags=re.IGNORECASE)
        protected_pairs.append(tags)
        prepped.append(gtext)

    out_texts: List[str] = []
    total = len(prepped)
    done = 0
    if progress_cb:
        progress_cb(total, done)

    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    headers = {"Content-Type": "application/json"}
    if api_key and api_key.lower() != "none":
        headers["Authorization"] = f"Bearer {api_key}"

    for i in range(0, total, max(1, batch_size)):
        batch = prepped[i : i + batch_size]

        numbered = [f"[{j}] {line}" for j, line in enumerate(batch)]
        user_msg = "\n".join(numbered)

        resp = requests.post(
            api_url,
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        # Accumulate usage
        usage = data.get("usage", {})
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)

        translated_text = data["choices"][0]["message"]["content"].strip()

        # Parse [N] markers from response
        results = {}
        for match in re.finditer(
            r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", translated_text, re.DOTALL
        ):
            idx = int(match.group(1))
            text = match.group(2).strip()
            results[idx] = text

        for j in range(len(batch)):
            out_texts.append(results.get(j, batch[j]))

        done = min(total, done + len(batch))
        if progress_cb:
            progress_cb(total, done)

    # Restore protected tags and normalize
    restored: List[str] = []
    for text, tags in zip(out_texts, protected_pairs):
        r = _restore_tags(text, tags)
        restored.append(_nfc(r))

    log.info("  API usage — prompt: %d, completion: %d, total: %d",
             total_usage["prompt_tokens"], total_usage["completion_tokens"],
             total_usage["total_tokens"])

    return restored


def translate_srt(
    srt_text: str,
    source_lang: str = "English",
    batch_size: int = 30,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    *,
    profile: dict,
    target_name: str = "Norwegian Bokmål",
) -> str:
    """Full SRT translation: parse → batch translate → reassemble."""
    blocks = _split_srt(srt_text)

    # Build groups to translate (only from text lines)
    groups: List[str] = []
    placements: List[Tuple[int, List[int]]] = []

    for bi, block in enumerate(blocks):
        if bi == 0 and block:
            block[0] = _strip_bom(block[0])

        text_idxs = [
            li for li, line in enumerate(block)
            if not _is_index_line(line) and not _is_time_line(line) and line is not None
        ]
        text_idxs = [li for li in text_idxs if block[li].strip() != ""]

        if not text_idxs:
            continue

        run: List[int] = []
        for li in text_idxs:
            line = block[li]
            if _is_allcaps_marker(line):
                if run:
                    merged = f" {_SENTINEL} ".join(block[k] for k in run)
                    groups.append(merged)
                    placements.append((bi, run[:]))
                    run.clear()
                groups.append(line)
                placements.append((bi, [li]))
            else:
                run.append(li)

        if run:
            merged = f" {_SENTINEL} ".join(block[k] for k in run)
            groups.append(merged)
            placements.append((bi, run[:]))

    if not groups:
        return _join_srt(blocks)

    # Only apply English-specific glossary when source is English
    glossary: Dict[str, str] = {}
    if source_lang.lower() == "english":
        glossary = {
            "removal men": "movers",
            "removals men": "movers",
        }

    translated = _llm_translate_batched(
        groups, source_lang,
        batch_size=batch_size, glossary=glossary,
        progress_cb=progress_cb,
        api_url=profile["api_url"],
        model=profile["model"],
        api_key=profile["api_key"],
        target_name=target_name,
    )

    # Place translated strings back
    gi = 0
    for (bi, idxs) in placements:
        text = _nfc(translated[gi] if gi < len(translated) else "")
        gi += 1

        if len(idxs) == 1:
            blocks[bi][idxs[0]] = text.strip()
            continue

        parts = [p.strip() for p in text.split(_SENTINEL)]
        if len(parts) != len(idxs):
            nl_parts = [p.strip() for p in re.split(r"\r?\n", text) if p.strip()]
            if len(nl_parts) == len(idxs):
                parts = nl_parts
            else:
                orig_lens = [len(blocks[bi][k]) for k in idxs]
                parts = _split_to_n_lines_preserving_words(text, len(idxs), target_lengths=orig_lens)

        for li, part in zip(idxs, parts):
            blocks[bi][li] = part

    return _join_srt(blocks)


# ══════════════════════════════════════════════════════════════════════════════
# ffprobe / ffmpeg helpers
# ══════════════════════════════════════════════════════════════════════════════


def run_ffprobe(path: Path) -> list[dict]:
    """Return subtitle stream metadata from a media file as a list of dicts."""
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
        streams = data.get("streams", [])
        return [s for s in streams if s.get("codec_type") == "subtitle"]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def has_target_embedded(streams: list[dict], target_codes: set[str]) -> bool:
    """Check if any subtitle stream has a language tag matching the target."""
    for s in streams:
        lang = (s.get("tags") or {}).get("language", "").lower()
        if lang in target_codes:
            return True
    return False


def _is_hi_track(stream: dict) -> bool:
    """Check if a subtitle track is marked as HI/SDH."""
    tags = stream.get("tags") or {}
    title = tags.get("title", "").lower()
    lang = tags.get("language", "").lower()
    combined = f"{title} {lang}"
    return any(p in combined for p in HI_PATTERNS)


# ── Generalized subtitle finders ─────────────────────────────────────────────


def find_best_text_sub(streams: list[dict], lang_codes: set[str]) -> dict | None:
    """Find the best text-based subtitle track for the given language codes, preferring non-HI."""
    candidates = []
    for s in streams:
        lang = (s.get("tags") or {}).get("language", "").lower()
        codec = s.get("codec_name", "").lower()
        if lang in lang_codes and codec in TEXT_SUB_CODECS:
            candidates.append(s)

    if not candidates:
        return None

    non_hi = [s for s in candidates if not _is_hi_track(s)]
    if non_hi:
        return non_hi[0]
    return candidates[0]


def has_bitmap_only(streams: list[dict], lang_codes: set[str]) -> bool:
    """True if subs exist for given language codes but ALL are bitmap (PGS/DVD)."""
    has_any = False
    has_text = False
    for s in streams:
        lang = (s.get("tags") or {}).get("language", "").lower()
        codec = s.get("codec_name", "").lower()
        if lang not in lang_codes:
            continue
        has_any = True
        if codec in TEXT_SUB_CODECS:
            has_text = True
    return has_any and not has_text


def find_untagged_text_subs(streams: list[dict]) -> list[dict]:
    """Find subtitle tracks with no language tag that are text-based."""
    results = []
    for s in streams:
        lang = (s.get("tags") or {}).get("language", "").strip().lower()
        codec = s.get("codec_name", "").lower()
        if not lang and codec in TEXT_SUB_CODECS:
            results.append(s)
    return results


# ── Sidecar detection ─────────────────────────────────────────────────────────


def find_target_sidecar(media_path: Path, target_codes: set[str]) -> Path | None:
    """Check if a sidecar subtitle for the target language already exists."""
    stem = media_path.stem
    parent = media_path.parent
    for code in sorted(target_codes):
        for ext in (".srt", ".ass"):
            candidate = parent / f"{stem}.{code}{ext}"
            if candidate.exists():
                return candidate
    return None


def find_sidecar(media_path: Path, lang_codes: set[str]) -> Path | None:
    """Find a sidecar subtitle for any of the given language codes."""
    stem = media_path.stem
    parent = media_path.parent
    # Prefer .srt over .ass
    for ext in (".srt", ".ass"):
        for code in sorted(lang_codes):
            candidate = parent / f"{stem}.{code}{ext}"
            if candidate.exists():
                return candidate
    return None


# ── Language detection for untagged tracks ────────────────────────────────────


def _extract_text_sample(media: Path, stream_index: int, max_cues: int = 10) -> str:
    """Extract a small text sample from a subtitle track."""
    tmp = Path(tempfile.mktemp(suffix=".srt"))
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
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


def _detect_track_language(
    media: Path,
    stream_index: int,
    *,
    profile: dict,
) -> str | None:
    """Extract text sample from an untagged track, ask LLM to identify language.

    Returns an ISO 639-1 code (e.g. 'en', 'no', 'es') or None on failure.
    """
    api_key = profile["api_key"]
    if not api_key or api_key.lower() == "none":
        return None

    sample = _extract_text_sample(media, stream_index)
    if not sample.strip():
        return None

    try:
        headers = {"Content-Type": "application/json"}
        if api_key.lower() != "none":
            headers["Authorization"] = f"Bearer {api_key}"

        resp = requests.post(
            profile["api_url"],
            headers=headers,
            json={
                "model": profile["model"],
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
                    {"role": "user", "content": sample[:500]},
                ],
                "temperature": 0.0,
            },
            timeout=15,
        )
        resp.raise_for_status()
        code = resp.json()["choices"][0]["message"]["content"].strip().lower()
        if re.match(r"^[a-z]{2,3}$", code):
            return code
        return None
    except Exception:
        return None


def _tag_track_language(media: Path, stream_index: int, lang_code: str) -> bool:
    """Tag an untagged subtitle track with its detected language via remux.

    Uses ffmpeg -c copy with -metadata:s:INDEX to set the language tag.
    Returns True on success.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mkv", dir=media.parent, prefix=".tmp_")
    os.close(tmp_fd)
    tmp_file = Path(tmp_path)

    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(media),
            "-map", "0",
            "-c", "copy",
            f"-metadata:s:{stream_index}", f"language={lang_code}",
            str(tmp_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)

        if result.returncode != 0:
            log.error("  Tag failed: %s", result.stderr[-200:] if result.stderr else "unknown")
            tmp_file.unlink(missing_ok=True)
            return False

        if not tmp_file.exists() or tmp_file.stat().st_size == 0:
            log.error("  Tag produced empty output")
            tmp_file.unlink(missing_ok=True)
            return False

        # Atomic swap
        backup = media.with_suffix(".mkv.bak")
        try:
            media.rename(backup)
            tmp_file.rename(media)
            backup.unlink()
            return True
        except Exception:
            if backup.exists() and not media.exists():
                backup.rename(media)
            tmp_file.unlink(missing_ok=True)
            raise

    except Exception as e:
        log.error("  Tag error: %s", e)
        tmp_file.unlink(missing_ok=True)
        return False


# ── Extraction & conversion ──────────────────────────────────────────────────


def extract_subtitle_track(media_path: Path, stream_index: int, output: Path) -> bool:
    """Extract a subtitle track from a media file to SRT using ffmpeg."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(media_path),
                "-map", f"0:{stream_index}",
                "-c:s", "text",
                str(output),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        return result.returncode == 0 and output.exists() and output.stat().st_size > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def convert_ass_to_srt(ass_path: Path, srt_output: Path) -> bool:
    """Convert an ASS/SSA file to SRT using ffmpeg."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(ass_path),
                "-c:s", "srt",
                str(srt_output),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return result.returncode == 0 and srt_output.exists() and srt_output.stat().st_size > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ── Translation ──────────────────────────────────────────────────────────────


def translate_file(
    srt_path: Path,
    output_path: Path,
    batch_size: int = 30,
    label: str = "",
    source_lang: str = "English",
    *,
    profile: dict,
    target_name: str = "Norwegian Bokmål",
) -> bool:
    """Read an SRT file, translate via LLM, write the result."""
    srt_text = srt_path.read_text(encoding="utf-8-sig")
    if not srt_text.strip():
        log.warning("  Empty SRT file: %s", srt_path)
        return False

    tag = label or output_path.stem

    def _progress(total: int, done: int) -> None:
        if total > 0:
            log.info("  %s: %d/%d groups translated", tag, done, total)

    result = translate_srt(
        srt_text,
        source_lang=source_lang,
        batch_size=batch_size,
        progress_cb=_progress,
        profile=profile,
        target_name=target_name,
    )
    output_path.write_text(result, encoding="utf-8")
    return output_path.exists() and output_path.stat().st_size > 0


# ══════════════════════════════════════════════════════════════════════════════
# Multi-language source selection
# ══════════════════════════════════════════════════════════════════════════════


def _find_source_sidecar(
    media: Path,
    source_languages: list[dict],
) -> tuple[str, Path] | None:
    """Try each source language in priority order, return (lang_name, sidecar_path) or None."""
    for lang in source_languages:
        codes = set(lang["codes"])
        sidecar = find_sidecar(media, codes)
        if sidecar:
            return (lang["name"], sidecar)
    return None


def _find_source_embedded(
    streams: list[dict],
    source_languages: list[dict],
) -> tuple[str, dict] | None:
    """Try each source language in priority order, return (lang_name, stream_dict) or None."""
    for lang in source_languages:
        codes = set(lang["codes"])
        # Skip if this language only has bitmap subs
        if has_bitmap_only(streams, codes):
            continue
        best = find_best_text_sub(streams, codes)
        if best:
            return (lang["name"], best)
    return None


# ── Job preparation (scan phase) ─────────────────────────────────────────────

# A translation job: everything needed to translate one file, resolved during scan.
TranslateJob = dict  # keys: media, rel, output, srt_source, temp_files, description, source_lang


def _prepare_jobs(
    folder: Path,
    dry_run: bool,
    force: bool,
    source_languages: list[dict],
    skip_detect: bool,
    skip_clean: bool,
    profile: dict,
    target_lang: dict,
) -> tuple[list[TranslateJob], dict]:
    """Scan folder, resolve what needs translating.

    Returns (jobs_to_translate, stats).
    Stats counts skipped files; jobs still need translation.
    For skipped MKVs, also runs a quick clean check if not skip_clean.
    """
    stats = {"total": 0, "skipped": 0, "translated": 0, "errors": 0,
             "cleaned": 0, "tracks_removed": 0, "clean_errors": 0}

    target_codes = target_lang["_codes_set"]
    sidecar_code = target_lang["sidecar_code"]
    keep_languages = target_lang["_keep_languages"]

    video_files = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    log.info("Found %d video files in %s", len(video_files), folder)

    # Build a set of all source language codes (for bitmap-only check message)
    all_source_codes: set[str] = set()
    for lang in source_languages:
        all_source_codes.update(lang["codes"])

    jobs: list[TranslateJob] = []

    def _skip_and_clean(media_path: Path, rel_path, reason: str) -> None:
        """Log skip, clean the MKV if needed, increment skip counter."""
        log.info("[SKIP] %s: %s", reason, rel_path)
        stats["skipped"] += 1
        if not skip_clean and not dry_run and media_path.suffix.lower() == ".mkv":
            _clean_single_file(media_path, rel_path, stats, keep_languages)

    for media in video_files:
        stats["total"] += 1
        rel = media.relative_to(folder)
        output_path = media.parent / f"{media.stem}.{sidecar_code}.srt"

        # ── Step 1: Output already exists? ────────────────────────────────
        if output_path.exists() and output_path.stat().st_size > 0 and not force:
            _skip_and_clean(media, rel, "Output exists")
            continue

        # ── Step 2: Target language sidecar? ─────────────────────────────
        target_sidecar = find_target_sidecar(media, target_codes)
        if target_sidecar:
            if force and target_sidecar == output_path:
                pass  # fall through to find source
            else:
                _skip_and_clean(media, rel, f"{target_lang['name']} sidecar")
                continue

        # ── Step 3: Target language embedded? ─────────────────────────────
        streams = run_ffprobe(media)
        if has_target_embedded(streams, target_codes):
            _skip_and_clean(media, rel, f"{target_lang['name']} embedded")
            continue

        # ── Step 4: Sidecar in any source language? ───────────────────────
        source_sidecar = _find_source_sidecar(media, source_languages)
        if source_sidecar:
            source_lang_name, sidecar_path = source_sidecar
            srt_source = sidecar_path
            temp_files: list[Path] = []

            if sidecar_path.suffix.lower() == ".ass":
                if dry_run:
                    log.info("[DRY-RUN] Would convert ASS + translate (%s): %s",
                             source_lang_name, rel)
                    stats["translated"] += 1
                    continue
                temp_srt = Path(tempfile.mktemp(suffix=".srt"))
                temp_files.append(temp_srt)
                log.info("[CONVERT] ASS→SRT: %s", sidecar_path.name)
                if not convert_ass_to_srt(sidecar_path, temp_srt):
                    log.error("[ERROR] ASS conversion failed: %s", rel)
                    stats["errors"] += 1
                    continue
                srt_source = temp_srt

            if dry_run:
                log.info("[DRY-RUN] Would translate sidecar (%s): %s",
                         source_lang_name, rel)
                stats["translated"] += 1
                continue

            jobs.append({
                "media": media, "rel": rel, "output": output_path,
                "srt_source": srt_source, "temp_files": temp_files,
                "description": f"Sidecar {sidecar_path.name} ({source_lang_name})",
                "source_lang": source_lang_name,
            })
            continue

        # ── Step 5: Embedded track in any source language? ────────────────
        source_embedded = _find_source_embedded(streams, source_languages)
        if source_embedded:
            source_lang_name, best_stream = source_embedded
            stream_idx = best_stream["index"]
            codec = best_stream.get("codec_name", "?")
            hi_note = " (HI)" if _is_hi_track(best_stream) else ""

            if dry_run:
                log.info("[DRY-RUN] Would extract%s idx=%d (%s, %s) + translate: %s",
                         hi_note, stream_idx, codec, source_lang_name, rel)
                stats["translated"] += 1
                continue

            temp_srt = Path(tempfile.mktemp(suffix=".srt"))
            log.info("[EXTRACT] idx=%d (%s%s, %s) from %s",
                     stream_idx, codec, hi_note, source_lang_name, rel)
            if not extract_subtitle_track(media, stream_idx, temp_srt):
                log.error("[ERROR] Extraction failed: %s", rel)
                stats["errors"] += 1
                continue

            jobs.append({
                "media": media, "rel": rel, "output": output_path,
                "srt_source": temp_srt, "temp_files": [temp_srt],
                "description": f"Embedded idx={stream_idx} ({codec}{hi_note}, {source_lang_name})",
                "source_lang": source_lang_name,
            })
            continue

        # ── Step 5b: Bitmap-only for any source language? ─────────────────
        if has_bitmap_only(streams, all_source_codes):
            _skip_and_clean(media, rel, "Source language bitmap only (needs OCR)")
            continue

        # ── Step 6: Untagged text tracks — detect language ────────────────
        if not skip_detect:
            untagged = find_untagged_text_subs(streams)
            if untagged:
                # Try the first untagged text track
                track = untagged[0]
                track_idx = track["index"]
                codec = track.get("codec_name", "?")
                log.info("[DETECT] idx=%d %s — detecting language...", track_idx, codec)

                detected_code = _detect_track_language(media, track_idx, profile=profile)
                if detected_code:
                    # Tag the track with detected language in the MKV
                    if media.suffix.lower() == ".mkv":
                        log.info("[TAG] idx=%d -> %s: %s", track_idx, detected_code, rel)
                        if not _tag_track_language(media, track_idx, detected_code):
                            log.warning("[TAG] Failed to tag idx=%d, continuing anyway: %s",
                                        track_idx, rel)

                    # Is it the target language? Skip.
                    if detected_code in target_codes:
                        _skip_and_clean(media, rel, f"Detected {target_lang['name']} (idx={track_idx})")
                        continue

                    # Is it in our source language list?
                    matched_lang = None
                    for lang in source_languages:
                        if detected_code in lang["codes"]:
                            matched_lang = lang["name"]
                            break

                    if matched_lang:
                        if dry_run:
                            log.info("[DRY-RUN] Would extract idx=%d (detected %s) + translate: %s",
                                     track_idx, matched_lang, rel)
                            stats["translated"] += 1
                            continue

                        temp_srt = Path(tempfile.mktemp(suffix=".srt"))
                        log.info("[EXTRACT] idx=%d (detected %s) from %s",
                                 track_idx, matched_lang, rel)
                        if not extract_subtitle_track(media, track_idx, temp_srt):
                            log.error("[ERROR] Extraction failed: %s", rel)
                            stats["errors"] += 1
                            continue

                        jobs.append({
                            "media": media, "rel": rel, "output": output_path,
                            "srt_source": temp_srt, "temp_files": [temp_srt],
                            "description": f"Embedded idx={track_idx} (detected {matched_lang})",
                            "source_lang": matched_lang,
                        })
                        continue
                    else:
                        _skip_and_clean(media, rel,
                                        f"Detected '{detected_code}' (not in source list)")
                        continue
                else:
                    _skip_and_clean(media, rel,
                                    f"Language detection failed for idx={track_idx}")
                    continue

        # ── Step 7: Nothing found ─────────────────────────────────────────
        _skip_and_clean(media, rel, "No source subs found")

    return jobs, stats


# ── Per-file muxing ───────────────────────────────────────────────────────────

# Lazy-loaded mux_subs functions (set once on first use)
_mux_subs_available: bool | None = None
_mux_single = None


def _ensure_mux_subs_imported() -> bool:
    """Try to import mux_subs.mux_single_file once. Returns True if available."""
    global _mux_subs_available, _mux_single
    if _mux_subs_available is not None:
        return _mux_subs_available
    try:
        from mux_subs import mux_single_file
        _mux_single = mux_single_file
        _mux_subs_available = True
    except ImportError:
        log.warning("mux_subs.py not found — per-file muxing disabled")
        _mux_subs_available = False
    return _mux_subs_available


def _mux_single_file(
    media: Path, rel, keep_sidecar: bool, stats: dict,
    sidecar_code: str = "no", mkv_tag: str = "nob", target_name: str = "Norwegian",
) -> None:
    """Mux the translated sidecar into the MKV. Thread-safe."""
    if media.suffix.lower() != ".mkv":
        return

    if not _ensure_mux_subs_imported():
        return

    srt_path = media.parent / f"{media.stem}.{sidecar_code}.srt"
    if not srt_path.exists():
        return

    log.info("[MUX] Muxing %s into %s", srt_path.name, media.name)
    if _mux_single(media, srt_path, keep_sidecar=keep_sidecar,
                    mkv_tag=mkv_tag, target_name=target_name):
        log.info("[MUX OK] %s", rel)
        with _stats_lock:
            stats["muxed"] += 1
    else:
        log.error("[MUX ERROR] %s", rel)
        with _stats_lock:
            stats["mux_errors"] += 1


# ── Per-file cleaning ─────────────────────────────────────────────────────────

# Lazy-loaded clean_subs functions (set once on first use)
_clean_subs_available: bool | None = None
_classify_tracks = None
_remux_without = None
_clean_ffprobe = None


def _ensure_clean_subs_imported() -> bool:
    """Try to import clean_subs functions once. Returns True if available."""
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
        log.warning("clean_subs.py not found — per-file cleaning disabled")
        _clean_subs_available = False
    return _clean_subs_available


def _clean_single_file(media: Path, rel, stats: dict,
                       keep_languages: set[str] | None = None) -> None:
    """Clean unwanted subtitle tracks from a single MKV after translation.

    Thread-safe: each thread cleans its own file, no shared state.
    """
    if media.suffix.lower() != ".mkv":
        return

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
        log.info("[CLEAN] Nothing to remove: %s", rel)
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
        with _stats_lock:
            stats["cleaned"] += 1
            stats["tracks_removed"] += len(remove)
    else:
        log.error("[CLEAN ERROR] Remux failed: %s", rel)
        with _stats_lock:
            stats["clean_errors"] += 1



# ── Translation worker ───────────────────────────────────────────────────────

_stats_lock = threading.Lock()


def _translate_one(
    job: TranslateJob, batch_size: int, stats: dict, profile: dict,
    skip_clean: bool = False,
    keep_sidecar: bool = False,
    target_lang: dict | None = None,
) -> None:
    """Translate a single job, then mux + clean the MKV. Thread-safe."""
    tl = target_lang or {}
    target_name = tl.get("name", "Norwegian Bokmål")
    sidecar_code = tl.get("sidecar_code", "no")
    mkv_tag = tl.get("mkv_tag", "nob")
    keep_languages = tl.get("_keep_languages")

    rel = job["rel"]
    media = job["media"]
    output_path = job["output"]
    srt_source = job["srt_source"]
    temp_files = job["temp_files"]
    source_lang = job.get("source_lang", "English")

    log.info("[TRANSLATE] %s → %s", job["description"], output_path.name)
    translated_ok = False
    try:
        ok = translate_file(
            srt_source, output_path, batch_size,
            label=str(rel), source_lang=source_lang,
            profile=profile, target_name=target_name,
        )
        if ok:
            log.info("[OK] %s", rel)
            with _stats_lock:
                stats["translated"] += 1
            translated_ok = True
        else:
            log.error("[ERROR] No output: %s", rel)
            with _stats_lock:
                stats["errors"] += 1
    except Exception as e:
        log.error("[ERROR] %s: %s", rel, e)
        with _stats_lock:
            stats["errors"] += 1
        if output_path.exists():
            output_path.unlink()
    finally:
        for tmp in temp_files:
            if tmp.exists():
                tmp.unlink()

    # Mux the sidecar into the MKV (before clean, so target track is embedded)
    if translated_ok and media.suffix.lower() == ".mkv":
        try:
            _mux_single_file(media, rel, keep_sidecar, stats,
                             sidecar_code=sidecar_code, mkv_tag=mkv_tag,
                             target_name=target_name)
        except Exception as e:
            log.error("[MUX ERROR] %s: %s", rel, e)
            with _stats_lock:
                stats["mux_errors"] += 1

    # Clean the MKV (after mux, so target language is kept)
    if translated_ok and not skip_clean:
        try:
            _clean_single_file(media, rel, stats, keep_languages)
        except Exception as e:
            log.error("[CLEAN ERROR] %s: %s", rel, e)
            with _stats_lock:
                stats["clean_errors"] += 1


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════


def scan_and_translate(
    folder: Path,
    batch_size: int = 500,
    dry_run: bool = False,
    parallel: int = 1,
    limit: int = 0,
    force: bool = False,
    source_languages: list[dict] | None = None,
    skip_detect: bool = False,
    skip_clean: bool = False,
    keep_sidecar: bool = False,
    profile: dict | None = None,
    target_lang: dict | None = None,
) -> dict:
    """Scan folder and translate subtitles, optionally in parallel."""
    if source_languages is None:
        source_languages = [{"codes": ["en", "eng"], "name": "English"}]
    if profile is None:
        profile = {"api_url": "", "model": "", "api_key": "", "name": "none"}
    if target_lang is None:
        target_lang = get_target_language({})

    jobs, stats = _prepare_jobs(
        folder, dry_run, force=force,
        source_languages=source_languages,
        skip_detect=skip_detect,
        skip_clean=skip_clean,
        profile=profile,
        target_lang=target_lang,
    )

    # Add mux stats counters (clean stats already initialized by _prepare_jobs)
    stats["muxed"] = 0
    stats["mux_errors"] = 0

    if not jobs or dry_run:
        return stats

    if limit > 0 and len(jobs) > limit:
        log.info("Limiting to %d of %d files", limit, len(jobs))
        jobs = jobs[:limit]

    log.info("Translating %d files (parallel=%d, batch_size=%d)", len(jobs), parallel, batch_size)

    if parallel <= 1:
        for job in jobs:
            _translate_one(job, batch_size, stats, profile,
                           skip_clean=skip_clean, keep_sidecar=keep_sidecar,
                           target_lang=target_lang)
    else:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(_translate_one, job, batch_size, stats, profile,
                            skip_clean=skip_clean, keep_sidecar=keep_sidecar,
                            target_lang=target_lang): job
                for job in jobs
            }
            for future in as_completed(futures):
                future.result()

    return stats


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════


def main():
    # Load config early so we can list profiles in --help
    config = load_config()
    available_profiles = list(config.get("profiles", {}).keys())
    default_profile = config.get("default_profile", "deepseek")
    target_lang = get_target_language(config)
    sidecar_ext = f".{target_lang['sidecar_code']}.srt"

    parser = argparse.ArgumentParser(
        description=f"Translate subtitles to {target_lang['name']} for a TV/movie folder.",
        epilog=f"Available LLM profiles: {', '.join(available_profiles)}  (default: {default_profile})",
    )
    parser.add_argument("folder", help="Path to folder to scan recursively")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Subtitle groups per LLM API call (default: 500)")
    parser.add_argument("--parallel", type=int, default=3,
                        help="Number of files to translate concurrently (default: 3)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of files to translate (0 = unlimited)")
    parser.add_argument("--force", action="store_true",
                        help=f"Retranslate even if {sidecar_ext} already exists")
    parser.add_argument("--log-file", type=str, default="",
                        help="Also write log output to this file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be translated without making API calls")
    parser.add_argument("--profile", type=str, default=None,
                        help=f"LLM profile to use (default: {default_profile})")
    parser.add_argument("--skip-clean", action="store_true",
                        help="Skip post-translation cleanup of unwanted subtitle tracks from MKVs")
    parser.add_argument("--keep-sidecar", action="store_true",
                        help=f"Keep {sidecar_ext} sidecar files after muxing into MKV")
    parser.add_argument("--skip-detect", action="store_true",
                        help="Skip language detection for untagged subtitle tracks")
    args = parser.parse_args()

    # Set up logging — console + optional file
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    # Resolve LLM profile
    profile = resolve_profile(config, args.profile)
    source_languages = get_source_languages(config)

    # Check API key (unless dry-run or key is "none" for local models)
    if not profile["api_key"] and profile["api_key"] != "none" and not args.dry_run:
        # Check if we need a key (api_key_env was set but env var is empty)
        profile_cfg = config["profiles"][profile["name"]]
        if "api_key_env" in profile_cfg:
            log.error("%s not set in .env — aborting.", profile_cfg["api_key_env"])
            sys.exit(1)

    folder = Path(args.folder)
    if not folder.is_dir():
        log.error("Not a directory: %s", folder)
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    if args.force:
        mode += " + FORCE"
    log.info("=== translate_subs [%s] ===", mode)
    log.info("Folder:     %s", folder)
    log.info("Target:     %s (sidecar: %s, MKV tag: %s)",
             target_lang["name"], sidecar_ext, target_lang["mkv_tag"])
    log.info("Profile:    %s (%s @ %s)", profile["name"], profile["model"], profile["api_url"])
    log.info("Batch size: %d / Parallel: %d", args.batch_size, args.parallel)
    log.info("Source languages: %s", ", ".join(l["name"] for l in source_languages))
    if args.skip_detect:
        log.info("Language detection: OFF (skipping untagged tracks)")
    if args.skip_clean:
        log.info("Post-processing: clean_subs DISABLED")
    if args.keep_sidecar:
        log.info("Sidecar: KEEP after mux")
    if args.log_file:
        log.info("Log file:   %s", args.log_file)

    start = time.time()
    stats = scan_and_translate(
        folder, batch_size=args.batch_size, dry_run=args.dry_run,
        parallel=args.parallel, limit=args.limit, force=args.force,
        source_languages=source_languages,
        skip_detect=args.skip_detect,
        skip_clean=args.skip_clean,
        keep_sidecar=args.keep_sidecar,
        profile=profile,
        target_lang=target_lang,
    )
    elapsed = time.time() - start

    log.info("=" * 60)
    log.info("  Total files:    %d", stats["total"])
    log.info("  Skipped:        %d", stats["skipped"])
    log.info("  Translated:     %d", stats["translated"])
    log.info("  Errors:         %d", stats["errors"])
    log.info("  MKVs muxed:     %d", stats.get("muxed", 0))
    if stats.get("mux_errors", 0) > 0:
        log.info("  Mux errors:     %d", stats["mux_errors"])
    if not args.skip_clean:
        log.info("  MKVs cleaned:   %d", stats.get("cleaned", 0))
        log.info("  Tracks removed: %d", stats.get("tracks_removed", 0))
        if stats.get("clean_errors", 0) > 0:
            log.info("  Clean errors:   %d", stats["clean_errors"])
    log.info("  Time:           %.1fs", elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
