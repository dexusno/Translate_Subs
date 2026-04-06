"""
translate_subs.py — Scan a media folder, find subtitles in any supported
source language, translate them to a configurable target language via LLM.

Target language, source languages, and LLM provider are configured in
llm_config.json next to this script.

Usage:
    python translate_subs.py "D:\\Movies\\Some Movie"
    python translate_subs.py --batch-size 350 --parallel 8 --dry-run "/mnt/media/Tv/Ugly Betty"
    python translate_subs.py --profile openai "D:\\TvSeries\\Breaking Bad"
"""

import argparse
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Tuple

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
        "timeout": p.get("timeout", 120),
        "batch_size": p.get("batch_size", 350),
        "parallel": p.get("parallel", 3),
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
        f"/no_think\n"
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
    api_timeout: int = 120,
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

    max_retries = 1  # retry once if batch comes back untranslated

    for i in range(0, total, max(1, batch_size)):
        batch = prepped[i : i + batch_size]

        numbered = [f"[{j}] {line}" for j, line in enumerate(batch)]
        user_msg = "\n".join(numbered)

        batch_results: list[str] = []
        for attempt in range(1 + max_retries):
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
                timeout=api_timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # Accumulate usage
            usage = data.get("usage", {})
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

            translated_text = data["choices"][0]["message"]["content"].strip()
            finish_reason = (data.get("choices", [{}])[0]
                            .get("finish_reason", ""))

            if finish_reason == "length":
                log.warning("  Batch %d-%d: response truncated "
                            "(finish_reason=length), output may be "
                            "incomplete",
                            i, i + len(batch))

            # Parse [N] markers from response
            results = {}
            for match in re.finditer(
                r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", translated_text, re.DOTALL
            ):
                idx = int(match.group(1))
                text = match.group(2).strip()
                results[idx] = text

            batch_results = [results.get(j, batch[j]) for j in range(len(batch))]

            # Verify translation actually happened.
            # Find where the translation stopped — first run of 5+ consecutive
            # unchanged lines indicates the LLM stopped translating.
            fail_start = -1
            if len(batch) >= 6:
                current_run = 0
                for k, (src, dst) in enumerate(zip(batch, batch_results)):
                    if src.strip().lower() == dst.strip().lower():
                        current_run += 1
                        if current_run >= 5 and fail_start < 0:
                            fail_start = k - current_run + 1
                    else:
                        current_run = 0

            if fail_start >= 0 and attempt < max_retries:
                # Keep the good part, resend only the failed tail
                good_count = fail_start
                fail_count = len(batch) - fail_start
                log.warning("  Batch %d-%d: translation stopped at "
                            "line %d, resending last %d lines...",
                            i, i + len(batch), fail_start, fail_count)

                # Resend just the failed portion as a smaller batch
                fail_batch = batch[fail_start:]
                fail_numbered = [f"[{j}] {line}"
                                 for j, line in enumerate(fail_batch)]
                fail_msg = "\n".join(fail_numbered)

                try:
                    resp2 = requests.post(
                        api_url, headers=headers,
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": fail_msg},
                            ],
                            "temperature": 0.3,
                        },
                        timeout=api_timeout,
                    )
                    resp2.raise_for_status()
                    data2 = resp2.json()

                    usage2 = data2.get("usage", {})
                    for k2 in total_usage:
                        total_usage[k2] += usage2.get(k2, 0)

                    text2 = data2["choices"][0]["message"]["content"].strip()
                    results2 = {}
                    for match in re.finditer(
                        r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", text2, re.DOTALL
                    ):
                        idx2 = int(match.group(1))
                        txt2 = match.group(2).strip()
                        results2[idx2] = txt2

                    # Patch the failed portion into batch_results
                    for j in range(len(fail_batch)):
                        if j in results2:
                            batch_results[fail_start + j] = results2[j]

                    log.info("  Batch %d-%d: recovered %d/%d failed lines",
                             i, i + len(batch),
                             len(results2), fail_count)
                except Exception as e:
                    log.warning("  Batch %d-%d: recovery failed: %s",
                                i, i + len(batch), e)

            elif fail_start >= 0:
                fail_count = len(batch) - fail_start
                log.warning("  Batch %d-%d: %d lines untranslated "
                            "from line %d, no retries left",
                            i, i + len(batch), fail_count, fail_start)
            break  # done with this batch

        out_texts.extend(batch_results)

        done = min(total, done + len(batch))
        if progress_cb:
            progress_cb(total, done)

    # Restore protected tags and normalize
    restored: List[str] = []
    for text, tags in zip(out_texts, protected_pairs):
        r = _restore_tags(text, tags)
        restored.append(_nfc(r))

    log.debug("  API usage — prompt: %d, completion: %d, total: %d",
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
        api_timeout=profile.get("timeout", 120),
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


def has_target_embedded(streams: list[dict], target_codes: set[str],
                        remove_bitmap: bool = True) -> bool:
    """Check if any subtitle stream has a language tag matching the target.

    When remove_bitmap is True (default), bitmap formats (PGS, DVD subs)
    are ignored — they don't count as having the target language, since
    they'll be removed during cleaning.
    """
    for s in streams:
        if remove_bitmap:
            codec = s.get("codec_name", "").lower()
            if codec in BITMAP_SUB_CODECS:
                continue
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


def _is_forced_track(stream: dict) -> bool:
    """Check if a subtitle track is marked as Forced."""
    tags = stream.get("tags") or {}
    title = tags.get("title", "").lower()
    disp = stream.get("disposition", {})
    return "forced" in title or disp.get("forced", 0) == 1


# ── Generalized subtitle finders ─────────────────────────────────────────────


def find_best_text_sub(streams: list[dict], lang_codes: set[str]) -> dict | None:
    """Find the best text-based subtitle track for the given language codes.

    Priority: non-forced non-HI > non-forced HI > forced.
    Forced tracks only contain foreign-language dialogue lines and are
    unsuitable as a translation source.
    """
    candidates = []
    for s in streams:
        lang = (s.get("tags") or {}).get("language", "").lower()
        codec = s.get("codec_name", "").lower()
        if lang in lang_codes and codec in TEXT_SUB_CODECS:
            candidates.append(s)

    if not candidates:
        return None

    # Best: non-forced, non-HI (regular full subs)
    regular = [s for s in candidates if not _is_forced_track(s) and not _is_hi_track(s)]
    if regular:
        return regular[0]

    # Next: non-forced HI/SDH (still full subs, just with descriptions)
    non_forced_hi = [s for s in candidates if not _is_forced_track(s)]
    if non_forced_hi:
        return non_forced_hi[0]

    # Last resort: forced track (partial subs, only foreign dialogue)
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


# ── Directory cache (avoids per-file network round-trips) ─────────────────────


class DirCache:
    """Cache of all filenames in a directory tree for fast sidecar lookups.

    On a local disk this makes no difference.  Over a network/VPN it eliminates
    thousands of individual ``Path.exists()`` stat calls — one ``rglob`` pass
    replaces them all.
    """

    def __init__(self, root: Path):
        self._files: set[Path] = set()
        log.debug("[CACHE] Building directory cache for %s ...", root)
        t0 = time.monotonic()
        for p in root.rglob("*"):
            # rglob yields dirs too — we only need files, but storing both
            # is fine since we only test membership via exact paths.
            self._files.add(p)
        elapsed = time.monotonic() - t0
        log.debug("[CACHE] Cached %d entries in %.1fs", len(self._files), elapsed)

    def exists(self, path: Path) -> bool:
        return path in self._files

    def add(self, path: Path) -> None:
        """Register a newly-created file so future lookups see it."""
        self._files.add(path)

    def remove(self, path: Path) -> None:
        """Unregister a file that was deleted."""
        self._files.discard(path)


# ── Sidecar detection ─────────────────────────────────────────────────────────

# Subtitle flag suffixes that appear between the language code and extension.
# e.g. "Episode.en.sdh.srt", "Episode.en.hi.srt", "Episode.en.forced.srt"
_SIDECAR_FLAG_SUFFIXES = ("", ".sdh", ".hi", ".cc", ".forced")


def find_target_sidecar(media_path: Path, target_codes: set[str],
                        cache: DirCache | None = None) -> Path | None:
    """Check if a sidecar subtitle for the target language already exists."""
    stem = media_path.stem
    parent = media_path.parent
    _exists = cache.exists if cache else lambda p: p.exists()
    for code in sorted(target_codes):
        for flag in _SIDECAR_FLAG_SUFFIXES:
            for ext in (".srt", ".ass"):
                candidate = parent / f"{stem}.{code}{flag}{ext}"
                if _exists(candidate):
                    return candidate
    return None


def find_sidecar(media_path: Path, lang_codes: set[str],
                 cache: DirCache | None = None) -> Path | None:
    """Find a sidecar subtitle for any of the given language codes."""
    stem = media_path.stem
    parent = media_path.parent
    _exists = cache.exists if cache else lambda p: p.exists()
    # Prefer .srt over .ass, prefer plain over flagged
    for ext in (".srt", ".ass"):
        for code in sorted(lang_codes):
            for flag in _SIDECAR_FLAG_SUFFIXES:
                candidate = parent / f"{stem}.{code}{flag}{ext}"
                if _exists(candidate):
                    return candidate
    return None


# ── Language detection for untagged tracks ────────────────────────────────────


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
            "ffmpeg", "-y", "-nostdin",
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
                "ffmpeg", "-y", "-nostdin",
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
                "ffmpeg", "-y", "-nostdin",
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
    cache: DirCache | None = None,
) -> tuple[str, Path] | None:
    """Try each source language in priority order, return (lang_name, sidecar_path) or None."""
    for lang in source_languages:
        codes = set(lang["codes"])
        sidecar = find_sidecar(media, codes, cache=cache)
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


def _generate_jobs(
    folder: Path,
    dry_run: bool,
    force: bool,
    source_languages: list[dict],
    skip_detect: bool,
    profile: dict,
    target_lang: dict,
    stats: dict,
    skipped_mkvs: list[Path],
    cache: DirCache | None = None,
    remove_bitmap: bool = True,
) -> Generator[TranslateJob, None, None]:
    """Scan folder, yield translation jobs one at a time as they're found.

    This is a generator — the caller can start translating the first job
    while scanning continues in the background.  Cleaning of skipped MKVs
    is deferred: their paths are collected in *skipped_mkvs* so the caller
    can clean them after all translation work is done.
    """
    target_codes = target_lang["_codes_set"]
    sidecar_code = target_lang["sidecar_code"]

    # Use cached listing when available for fast sidecar checks
    log.info("Scanning %s ...", folder)
    t0 = time.monotonic()
    video_files = sorted(
        f for f in folder.rglob("*")
        if f.suffix.lower() in VIDEO_EXTENSIONS
        and not f.name.startswith(".tmp_")
    )
    log.info("Found %d video files in %.1fs", len(video_files),
             time.monotonic() - t0)

    # Build a set of all source language codes (for bitmap-only check message)
    all_source_codes: set[str] = set()
    for lang in source_languages:
        all_source_codes.update(lang["codes"])

    def _skip(media_path: Path, rel_path, reason: str,
              is_has_target: bool = False) -> None:
        """Log skip, collect MKV for deferred cleaning, update stats."""
        log.debug("[SKIP] %s: %s", reason, rel_path)
        stats["skipped"] += 1
        if is_has_target:
            stats["has_target"].append(str(rel_path))
        if media_path.suffix.lower() == ".mkv":
            skipped_mkvs.append(media_path)

    for media in video_files:
        stats["total"] += 1
        rel = media.relative_to(folder)
        output_path = media.parent / f"{media.stem}.{sidecar_code}.srt"

        # ── Step 1: Output already exists? (cache lookup, no network) ─────
        out_exists = cache.exists(output_path) if cache else output_path.exists()
        if out_exists and not force:
            # Verify file is non-empty (one stat call, acceptable)
            try:
                if output_path.stat().st_size > 0:
                    _skip(media, rel, "Output exists", is_has_target=True)
                    continue
            except OSError:
                pass  # file vanished between cache and stat — proceed

        # ── Step 2: Target language sidecar? (cache lookup, no network) ───
        target_sidecar = find_target_sidecar(media, target_codes, cache=cache)
        if target_sidecar:
            if force and target_sidecar == output_path:
                pass  # fall through to find source
            else:
                _skip(media, rel, f"{target_lang['name']} sidecar",
                      is_has_target=True)
                continue

        # ── Step 3: Source sidecar? (cache lookup, no network) ────────────
        # Remember it but don't yield yet — we still need to check for
        # embedded target language (step 4) which requires ffprobe.
        source_sidecar = _find_source_sidecar(media, source_languages,
                                               cache=cache)

        # ── Step 4: Probe embedded tracks (ffprobe — first network I/O) ──
        # Only called if steps 1-2 didn't resolve. This is the expensive
        # part over VPN, but unavoidable for embedded-only decisions.
        streams = run_ffprobe(media)

        # Check for target language embedded
        if has_target_embedded(streams, target_codes,
                               remove_bitmap=remove_bitmap) and not force:
            _skip(media, rel, f"{target_lang['name']} embedded",
                  is_has_target=True)
            continue

        # ── Step 4b: Source sidecar found earlier? Now safe to use it. ────
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
                log.info("[CONVERT] ASS->SRT: %s", sidecar_path.name)
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

            yield {
                "media": media, "rel": rel, "output": output_path,
                "srt_source": srt_source, "temp_files": temp_files,
                "description": f"Sidecar {sidecar_path.name} ({source_lang_name})",
                "source_lang": source_lang_name,
            }
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
            log.debug("[EXTRACT] idx=%d (%s%s, %s) from %s",
                     stream_idx, codec, hi_note, source_lang_name, rel)
            if not extract_subtitle_track(media, stream_idx, temp_srt):
                log.error("[ERROR] Extraction failed: %s", rel)
                stats["errors"] += 1
                continue

            yield {
                "media": media, "rel": rel, "output": output_path,
                "srt_source": temp_srt, "temp_files": [temp_srt],
                "description": f"Embedded idx={stream_idx} ({codec}{hi_note}, {source_lang_name})",
                "source_lang": source_lang_name,
            }
            continue

        # ── Step 5b: Bitmap-only for any source language? ─────────────────
        if has_bitmap_only(streams, all_source_codes):
            stats["no_subs"].append(str(rel))
            _skip(media, rel, "Source language bitmap only (needs OCR)")
            continue

        # ── Step 5c: Fallback — any tagged text track not in priority list?
        if not skip_detect:
            _fallback_stream = None
            for s in streams:
                if s.get("codec_type") != "subtitle":
                    continue
                codec = s.get("codec_name", "").lower()
                if codec not in TEXT_SUB_CODECS:
                    continue
                lang = (s.get("tags") or {}).get("language", "").lower()
                if not lang:
                    continue  # untagged — handled in step 6
                if lang in target_codes:
                    continue  # target language — shouldn't reach here
                if lang in all_source_codes:
                    continue  # priority language — shouldn't reach here
                if _is_forced_track(s):
                    continue  # forced tracks are partial
                _fallback_stream = s
                break

            if _fallback_stream:
                fb_idx = _fallback_stream["index"]
                fb_codec = _fallback_stream.get("codec_name", "?")
                fb_lang = ((_fallback_stream.get("tags") or {})
                           .get("language", "?"))
                log.info("[FALLBACK] Embedded idx=%d (%s, %s) — "
                         "not in source list, using as fallback: %s",
                         fb_idx, fb_codec, fb_lang, rel)

                if dry_run:
                    log.info("[DRY-RUN] Would extract idx=%d "
                             "(%s fallback) + translate: %s",
                             fb_idx, fb_lang.upper(), rel)
                    stats["translated"] += 1
                    continue

                temp_srt = Path(tempfile.mktemp(suffix=".srt"))
                if not extract_subtitle_track(media, fb_idx, temp_srt):
                    log.error("[ERROR] Extraction failed: %s", rel)
                    stats["errors"] += 1
                    continue

                yield {
                    "media": media, "rel": rel, "output": output_path,
                    "srt_source": temp_srt, "temp_files": [temp_srt],
                    "description": (
                        f"Fallback embedded idx={fb_idx} "
                        f"({fb_codec}, {fb_lang})"),
                    "source_lang": fb_lang.upper(),
                }
                continue

        # ── Step 6: Untagged text tracks — detect language ────────────────
        if not skip_detect:
            untagged = find_untagged_text_subs(streams)
            if untagged:
                # Try the first untagged text track
                track = untagged[0]
                track_idx = track["index"]
                codec = track.get("codec_name", "?")
                log.info("[DETECT] idx=%d %s -- detecting language...",
                         track_idx, codec)

                detected_code = _detect_track_language(
                    media, track_idx, profile=profile)
                if detected_code:
                    # Tag the track with detected language in the MKV
                    if media.suffix.lower() == ".mkv":
                        log.info("[TAG] idx=%d -> %s: %s",
                                 track_idx, detected_code, rel)
                        if not _tag_track_language(media, track_idx,
                                                   detected_code):
                            log.warning(
                                "[TAG] Failed to tag idx=%d, continuing: %s",
                                track_idx, rel)

                    # Is it the target language? Skip.
                    if detected_code in target_codes:
                        _skip(media, rel,
                              f"Detected {target_lang['name']} (idx={track_idx})")
                        continue

                    # Is it in our source language list?
                    matched_lang = None
                    for lang in source_languages:
                        if detected_code in lang["codes"]:
                            matched_lang = lang["name"]
                            break

                    # Map detected code to a language name
                    if matched_lang:
                        detected_name = matched_lang
                    else:
                        # Not in priority list — use as fallback
                        detected_name = detected_code.upper()
                        log.info("[FALLBACK] idx=%d detected '%s' "
                                 "(not in source list, using as fallback): %s",
                                 track_idx, detected_code, rel)

                    if dry_run:
                        log.info(
                            "[DRY-RUN] Would extract idx=%d "
                            "(detected %s) + translate: %s",
                            track_idx, detected_name, rel)
                        stats["translated"] += 1
                        continue

                    temp_srt = Path(tempfile.mktemp(suffix=".srt"))
                    log.debug("[EXTRACT] idx=%d (detected %s) from %s",
                             track_idx, detected_name, rel)
                    if not extract_subtitle_track(media, track_idx,
                                                  temp_srt):
                        log.error("[ERROR] Extraction failed: %s", rel)
                        stats["errors"] += 1
                        continue

                    yield {
                        "media": media, "rel": rel, "output": output_path,
                        "srt_source": temp_srt, "temp_files": [temp_srt],
                        "description": (
                            f"Embedded idx={track_idx} "
                            f"(detected {detected_name})"),
                        "source_lang": detected_name,
                    }
                    continue
                else:
                    _skip(media, rel,
                          f"Language detection failed for idx={track_idx}")
                    continue

        # ── Step 7: Fallback — any external subtitle file we can read? ──
        # If no priority-list match was found, look for ANY external .srt/.ass
        # file and let the LLM detect and translate from whatever language it is.
        if not skip_detect:
            all_sc = _find_all_sidecars(media)
            for sc in all_sc:
                lang_code = _sidecar_lang_code(sc, media.stem)
                # Skip if it's our target language
                if lang_code and lang_code in target_codes:
                    continue
                # Skip bitmap-format files
                if sc.suffix.lower() not in (".srt", ".ass"):
                    continue
                # Read a sample to detect language
                try:
                    sample = sc.read_text(
                        encoding="utf-8-sig", errors="replace")[:2000]
                except OSError:
                    continue
                if not sample.strip():
                    continue

                # Determine source language name
                if lang_code:
                    fallback_name = lang_code.upper()
                else:
                    fallback_name = "auto-detect"

                log.info("[FALLBACK] External file %s (lang=%s): %s",
                         sc.name, fallback_name, rel)

                srt_source = sc
                temp_files_fb: list[Path] = []

                if sc.suffix.lower() == ".ass":
                    if dry_run:
                        log.info("[DRY-RUN] Would convert ASS + translate "
                                 "(%s fallback): %s", fallback_name, rel)
                        stats["translated"] += 1
                        break
                    temp_srt = Path(tempfile.mktemp(suffix=".srt"))
                    temp_files_fb.append(temp_srt)
                    if not convert_ass_to_srt(sc, temp_srt):
                        log.error("[ERROR] ASS conversion failed: %s", rel)
                        stats["errors"] += 1
                        break
                    srt_source = temp_srt

                if dry_run:
                    log.info("[DRY-RUN] Would translate (%s fallback): %s",
                             fallback_name, rel)
                    stats["translated"] += 1
                    break

                yield {
                    "media": media, "rel": rel, "output": output_path,
                    "srt_source": srt_source, "temp_files": temp_files_fb,
                    "description": (
                        f"Fallback {sc.name} ({fallback_name})"),
                    "source_lang": fallback_name,
                }
                break
            else:
                # for-else: no suitable fallback found
                stats["no_subs"].append(str(rel))
                _skip(media, rel, "No source subs found")
                continue
            # break landed here — job was yielded or dry-run logged
            continue

        # ── Step 8: Nothing found ─────────────────────────────────────────
        stats["no_subs"].append(str(rel))
        _skip(media, rel, "No source subs found")


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
                       keep_languages: set[str] | None = None,
                       remove_bitmap: bool = True) -> None:
    """Clean unwanted tracks + mux wanted sidecars + delete all sidecars.

    Used for skipped files (already have Norwegian) that may still have
    unwanted embedded tracks or redundant sidecar files.  Delegates to
    _mux_and_clean_single_file which handles everything in one pass.
    Thread-safe.
    """
    if media.suffix.lower() != ".mkv":
        return

    if not _ensure_clean_subs_imported():
        return

    _mux_and_clean_single_file(
        media, rel, keep_sidecar=False, skip_clean=False, stats=stats,
        keep_languages=keep_languages, remove_bitmap=remove_bitmap,
    )


# Language tag → MKV metadata language code mapping
_SIDECAR_MKV_TAGS = {
    "no": "nor", "nb": "nob", "nob": "nob", "nor": "nor", "nno": "nno",
    "en": "eng", "eng": "eng",
    "da": "dan", "dan": "dan",
    "sv": "swe", "swe": "swe",
}

# MKV language tag → clean display title
_LANG_TITLES = {
    "nor": "Norwegian", "nob": "Norwegian", "nno": "Norwegian",
    "eng": "English",
    "dan": "Danish",
    "swe": "Swedish",
}

# Sidecar file extensions to look for
_SIDECAR_EXTENSIONS = (".srt", ".ass")


def _find_all_sidecars(media: Path) -> list[Path]:
    """Find ALL subtitle sidecar files next to a media file."""
    stem = media.stem
    parent = media.parent
    sidecars = []
    for f in parent.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in _SIDECAR_EXTENSIONS:
            continue
        # Match files like "Episode.en.srt", "Episode.no.srt", "Episode.srt"
        if f.stem == stem or f.stem.startswith(stem + "."):
            sidecars.append(f)
    return sidecars


def _sidecar_lang_code(sidecar: Path, media_stem: str) -> str:
    """Extract the language code from a sidecar filename.

    Handles plain and flagged sidecars:
      "Episode.en.srt"      → "en"
      "Episode.en.sdh.srt"  → "en"
      "Episode.en.hi.srt"   → "en"
      "Episode.eng.forced.srt" → "eng"
      "Episode.srt"         → ""
    """
    # Remove the subtitle extension to get e.g. "Episode.en" or "Episode.en.sdh"
    name_no_ext = sidecar.stem
    if name_no_ext == media_stem:
        return ""  # no language code, e.g. "Episode.srt"
    suffix = name_no_ext[len(media_stem):]  # e.g. ".en" or ".en.sdh"
    parts = suffix.lstrip(".").lower().split(".")
    # Strip known flag suffixes (sdh, hi, cc, forced) to get the language code
    flags = {"sdh", "hi", "cc", "forced"}
    lang_parts = [p for p in parts if p not in flags]
    return lang_parts[0] if lang_parts else ""


def _mux_and_clean_single_file(
    media: Path, rel, keep_sidecar: bool, skip_clean: bool,
    stats: dict, sidecar_code: str = "no", mkv_tag: str = "nob",
    target_name: str = "Norwegian", keep_languages: set[str] | None = None,
    remove_bitmap: bool = True, force: bool = False,
) -> None:
    """Mux + clean + delete sidecars in ONE remux pass.

    Single ffmpeg call that:
      1. Maps all video + audio streams from the original MKV
      2. Maps only embedded subtitle tracks in kept languages (no/en/da/sv)
      3. Muxes in any sidecar subtitles for kept languages that aren't
         already embedded (Norwegian, English, Danish, Swedish)
      4. Removes all unwanted embedded tracks (Spanish, French, etc.)
      5. Writes one output file

    After the remux, ALL sidecar subtitle files are deleted — they're either
    now inside the MKV or unwanted.

    This does everything in one read+write pass — critical for VPN/network
    paths where each remux transfers the full multi-GB MKV.  Thread-safe.
    """
    if media.suffix.lower() != ".mkv":
        return

    langs_to_keep = keep_languages or set()

    # ── Probe the MKV ─────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=index,codec_type,codec_name:stream_tags=language,title",
             "-of", "json", str(media)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
        if result.returncode != 0:
            log.error("[MUX+CLEAN] ffprobe failed: %s", rel)
            with _stats_lock:
                stats["mux_errors"] += 1
            return
        streams = json.loads(result.stdout).get("streams", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        log.error("[MUX+CLEAN] ffprobe error: %s: %s", rel, e)
        with _stats_lock:
            stats["mux_errors"] += 1
        return

    # ── Classify embedded subtitle tracks ─────────────────────────────
    tracks_removed = 0
    if skip_clean or not _ensure_clean_subs_imported():
        keep_sub_indices = [s["index"] for s in streams
                           if s.get("codec_type") == "subtitle"]
    else:
        keep, remove = _classify_tracks(media, streams, skip_detect=True,
                                         keep_languages=langs_to_keep,
                                         remove_bitmap=remove_bitmap)
        keep_sub_indices = [s["index"] for s in keep]
        tracks_removed = len(remove)
        if remove:
            remove_langs = [(s.get("tags") or {}).get("language", "???")
                           for s in remove]
            log.debug("[MUX+CLEAN] Removing %d embedded track(s): %s",
                      tracks_removed, ", ".join(remove_langs))

    # Build set of languages already embedded (after cleaning)
    embedded_langs: set[str] = set()
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        if s["index"] in keep_sub_indices:
            lang = (s.get("tags") or {}).get("language", "").lower()
            if lang:
                embedded_langs.add(lang)

    # ── Find sidecars to mux in ───────────────────────────────────────
    all_sidecars = _find_all_sidecars(media)
    sidecars_to_mux: list[tuple[Path, str, str]] = []  # (path, lang_code, mkv_tag)
    sidecars_to_delete: list[Path] = []

    for sc in all_sidecars:
        lang = _sidecar_lang_code(sc, media.stem)

        if not lang:
            # Couldn't determine language — leave it alone
            log.debug("[MUX+CLEAN] Sidecar unknown, keeping: %s", sc.name)
            continue

        if lang not in langs_to_keep:
            # Unwanted language (Spanish, French, etc.) — safe to delete
            sidecars_to_delete.append(sc)
            continue

        # Wanted language — check if already embedded
        # Check against both the sidecar code and known aliases
        mkv_tag_for_lang = _SIDECAR_MKV_TAGS.get(lang, lang)
        already_embedded = lang in embedded_langs or mkv_tag_for_lang in embedded_langs

        if already_embedded and not force:
            # Already inside the MKV — safe to delete the redundant sidecar
            sidecars_to_delete.append(sc)
            log.debug("[MUX+CLEAN] Sidecar redundant (embedded): %s", sc.name)
        elif already_embedded and force:
            # Force mode — replace the old embedded track with the new sidecar.
            # Remove the old track from keep list so it gets dropped in the remux.
            keep_sub_indices = [
                idx for idx in keep_sub_indices
                if not any(
                    s["index"] == idx
                    and (s.get("tags") or {}).get("language", "").lower()
                        in (lang, mkv_tag_for_lang)
                    for s in streams
                )
            ]
            sidecars_to_mux.append((sc, lang, mkv_tag_for_lang))
            sidecars_to_delete.append(sc)
            embedded_langs.add(lang)
            embedded_langs.add(mkv_tag_for_lang)
            log.debug("[MUX+CLEAN] Force replacing embedded %s: %s",
                      lang, sc.name)
        else:
            # Not embedded — mux it in, then delete
            sidecars_to_mux.append((sc, lang, mkv_tag_for_lang))
            sidecars_to_delete.append(sc)  # delete after successful mux
            embedded_langs.add(lang)  # prevent dupes if multiple sidecars
            embedded_langs.add(mkv_tag_for_lang)
            log.debug("[MUX+CLEAN] Will mux sidecar: %s (lang=%s)",
                      sc.name, mkv_tag_for_lang)

    # ── Check for "nob" tags that should be "nor" ───────────────────
    # Retag Norwegian Bokmål → Norwegian in kept tracks
    nob_retag_indices: list[tuple[int, int]] = []  # (stream_index, sub_position)
    _sub_pos = 0
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        if s["index"] in keep_sub_indices:
            lang = (s.get("tags") or {}).get("language", "").lower()
            if lang == "nob":
                nob_retag_indices.append((s["index"], _sub_pos))
            _sub_pos += 1

    # ── Check if any work is needed ───────────────────────────────────
    needs_remux = (sidecars_to_mux or tracks_removed > 0
                   or len(nob_retag_indices) > 0)

    if not needs_remux:
        # Nothing to mux, nothing to clean, no retags — just delete sidecars
        if sidecars_to_delete:
            for sc in sidecars_to_delete:
                if not keep_sidecar:
                    sc.unlink(missing_ok=True)
                    log.debug("[MUX+CLEAN] Deleted sidecar: %s", sc.name)
            log.debug("[MUX+CLEAN] No remux needed, cleaned %d sidecar(s): %s",
                      len(sidecars_to_delete), rel)
        else:
            log.debug("[MUX+CLEAN] Nothing to do: %s", rel)
        return

    if nob_retag_indices:
        log.debug("[MUX+CLEAN] Retagging %d track(s) nob -> nor: %s",
                  len(nob_retag_indices), rel)

    # ── Build ffmpeg command ──────────────────────────────────────────
    # Input 0: original MKV
    # Inputs 1..N: sidecar files to mux in
    input_args = ["-i", str(media)]
    for sc_path, _, _ in sidecars_to_mux:
        input_args.extend(["-i", str(sc_path)])

    # Map: all video + audio from input 0
    map_args = ["-map", "0:v", "-map", "0:a"]

    # Map kept embedded subtitle tracks + retag nob → nor + fix titles
    metadata_args = []
    sub_track_idx = 0
    for idx in sorted(keep_sub_indices):
        map_args.extend(["-map", f"0:{idx}"])
        # Check if this track needs retagging
        for stream_idx, _ in nob_retag_indices:
            if stream_idx == idx:
                title = _LANG_TITLES.get("nor", target_name)
                metadata_args.extend([
                    f"-metadata:s:s:{sub_track_idx}", "language=nor",
                    f"-metadata:s:s:{sub_track_idx}", f"title={title}",
                ])
                break
        sub_track_idx += 1

    # Map sidecar inputs (input 1, 2, 3, ...) with language + title
    for i, (_, _, tag) in enumerate(sidecars_to_mux):
        input_num = i + 1  # input 0 is the MKV
        map_args.extend(["-map", f"{input_num}:0"])
        title = _LANG_TITLES.get(tag, target_name)
        metadata_args.extend([
            f"-metadata:s:s:{sub_track_idx}", f"language={tag}",
            f"-metadata:s:s:{sub_track_idx}", f"title={title}",
        ])
        sub_track_idx += 1

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mkv", dir=media.parent,
                                        prefix=".tmp_")
    os.close(tmp_fd)
    tmp_file = Path(tmp_path)

    try:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            *input_args,
            *map_args,
            "-c", "copy",
            *metadata_args,
            str(tmp_file),
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600,
        )

        if result.returncode != 0:
            log.error("[MUX+CLEAN] ffmpeg failed: %s",
                      result.stderr[-200:] if result.stderr else "unknown")
            tmp_file.unlink(missing_ok=True)
            with _stats_lock:
                stats["mux_errors"] += 1
            return

        if not tmp_file.exists() or tmp_file.stat().st_size == 0:
            log.error("[MUX+CLEAN] ffmpeg produced empty output: %s", rel)
            tmp_file.unlink(missing_ok=True)
            with _stats_lock:
                stats["mux_errors"] += 1
            return

        # Atomic swap: original → backup → swap → delete backup
        original_size = media.stat().st_size
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

        new_size = media.stat().st_size

        # Delete all sidecars (muxed ones + redundant ones + unwanted ones)
        if not keep_sidecar:
            for sc in sidecars_to_delete:
                sc.unlink(missing_ok=True)

        with _stats_lock:
            stats["muxed"] += 1
            if tracks_removed > 0:
                stats["cleaned"] += 1
                stats["tracks_removed"] += tracks_removed

        # Summary log
        parts = []
        if sidecars_to_mux:
            muxed_names = [sc.name for sc, _, _ in sidecars_to_mux]
            parts.append(f"muxed {', '.join(muxed_names)}")
        if tracks_removed > 0:
            parts.append(f"removed {tracks_removed} track(s)")
        if nob_retag_indices:
            parts.append(f"retagged {len(nob_retag_indices)} nob->nor")
        if sidecars_to_delete and not keep_sidecar:
            parts.append(f"deleted {len(sidecars_to_delete)} sidecar(s)")
        saved_mb = (original_size - new_size) / (1024 * 1024)
        if saved_mb > 0.1:
            parts.append(f"saved {saved_mb:.1f} MB")
        log.info("[MUX+CLEAN OK] %s -- %s", rel, ", ".join(parts))

    except Exception as e:
        log.error("[MUX+CLEAN ERROR] %s: %s", rel, e)
        tmp_file.unlink(missing_ok=True)
        with _stats_lock:
            stats["mux_errors"] += 1



# ── Translation worker ───────────────────────────────────────────────────────

_stats_lock = threading.Lock()


def _translate_one(
    job: TranslateJob, batch_size: int, stats: dict, profile: dict,
    skip_clean: bool = False,
    keep_sidecar: bool = False,
    target_lang: dict | None = None,
    remove_bitmap: bool = True,
    force: bool = False,
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
                stats["translated_files"].append(str(rel))
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

    # Mux sidecar into MKV + clean unwanted tracks in one remux pass
    if translated_ok and media.suffix.lower() == ".mkv":
        try:
            _mux_and_clean_single_file(
                media, rel, keep_sidecar, skip_clean, stats,
                sidecar_code=sidecar_code, mkv_tag=mkv_tag,
                target_name=target_name, keep_languages=keep_languages,
                remove_bitmap=remove_bitmap, force=force,
            )
        except Exception as e:
            log.error("[MUX+CLEAN ERROR] %s: %s", rel, e)
            with _stats_lock:
                stats["mux_errors"] += 1


# ══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════════════


def scan_and_translate(
    folder: Path,
    batch_size: int = 350,
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
    remove_bitmap: bool = True,
) -> dict:
    """Scan folder and translate subtitles using a streaming pipeline.

    Instead of scanning everything first, this uses a producer–consumer
    pattern: the scanner yields jobs as soon as they're found, and
    translation workers start immediately.  Over network/VPN paths this
    can cut the perceived startup time from minutes to seconds.
    """
    if source_languages is None:
        source_languages = [{"codes": ["en", "eng"], "name": "English"}]
    if profile is None:
        profile = {"api_url": "", "model": "", "api_key": "", "name": "none"}
    if target_lang is None:
        target_lang = get_target_language({})

    keep_languages = target_lang["_keep_languages"]

    stats = {"total": 0, "skipped": 0, "translated": 0, "errors": 0,
             "cleaned": 0, "tracks_removed": 0, "clean_errors": 0,
             "muxed": 0, "mux_errors": 0,
             "no_subs": [], "translated_files": [], "has_target": []}

    # Build directory cache once — replaces thousands of exists() calls
    cache = DirCache(folder)

    # Skipped MKVs are collected for deferred cleaning (after translation)
    skipped_mkvs: list[Path] = []

    job_gen = _generate_jobs(
        folder, dry_run, force=force,
        source_languages=source_languages,
        skip_detect=skip_detect,
        profile=profile,
        target_lang=target_lang,
        stats=stats,
        skipped_mkvs=skipped_mkvs,
        cache=cache,
        remove_bitmap=remove_bitmap,
    )

    if dry_run:
        # Just exhaust the generator to collect stats
        for _ in job_gen:
            pass
        return stats

    # ── Streaming pipeline: scan + translate concurrently ─────────────
    submitted = 0
    futures: dict = {}

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        for job in job_gen:
            if limit > 0 and submitted >= limit:
                log.info("Limit reached (%d files), stopping scan", limit)
                break

            future = pool.submit(
                _translate_one, job, batch_size, stats, profile,
                skip_clean=skip_clean, keep_sidecar=keep_sidecar,
                target_lang=target_lang, remove_bitmap=remove_bitmap,
                force=force,
            )
            futures[future] = job
            submitted += 1

            # Log that translation started while scan continues
            if submitted == 1:
                log.info("First job submitted, translation started "
                         "(scan continues in background)")

            # Harvest completed futures without blocking
            done_futures = [f for f in futures if f.done()]
            for f in done_futures:
                f.result()  # propagate exceptions
                del futures[f]

        # Wait for remaining translations to finish
        for future in as_completed(futures):
            future.result()

    if submitted > 0:
        log.info("All %d translation(s) complete", submitted)

    # ── Deferred cleaning of skipped MKVs ─────────────────────────────
    if not skip_clean and skipped_mkvs:
        log.info("Cleaning %d skipped MKV(s) ...", len(skipped_mkvs))
        for media in skipped_mkvs:
            try:
                rel = media.relative_to(folder)
                _clean_single_file(media, rel, stats, keep_languages,
                                   remove_bitmap=remove_bitmap)
            except Exception as e:
                log.error("[CLEAN ERROR] %s: %s", media.name, e)
                stats["clean_errors"] += 1

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
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Subtitle groups per LLM API call (default: from profile or 350)")
    parser.add_argument("--parallel", type=int, default=None,
                        help="Number of files to translate concurrently (default: from profile or 3)")
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

    # Apply profile defaults for batch_size and parallel, CLI overrides if specified
    batch_size = args.batch_size if args.batch_size is not None else profile["batch_size"]
    parallel = args.parallel if args.parallel is not None else profile["parallel"]

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
    log.info("Batch size: %d / Parallel: %d", batch_size, parallel)
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
    remove_bitmap = config.get("remove_bitmap_subs", True)

    stats = scan_and_translate(
        folder, batch_size=batch_size, dry_run=args.dry_run,
        parallel=parallel, limit=args.limit, force=args.force,
        source_languages=source_languages,
        skip_detect=args.skip_detect,
        skip_clean=args.skip_clean,
        keep_sidecar=args.keep_sidecar,
        profile=profile,
        target_lang=target_lang,
        remove_bitmap=remove_bitmap,
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
    has_target = stats.get("has_target", [])
    if has_target:
        log.info("  Already done:   %d files (Norwegian found)", len(has_target))
    no_subs = stats.get("no_subs", [])
    if no_subs:
        log.info("  No subs found:  %d files (see report log)", len(no_subs))
    log.info("  Time:           %.1fs", elapsed)
    log.info("=" * 60)

    # Write report log to the script's directory
    folder_name = folder.name or folder.parts[-1] if folder.parts else "unknown"
    safe_name = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in folder_name)
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    report_path = logs_dir / f"{safe_name}.log"
    with open(report_path, "w", encoding="utf-8") as rpt:
        rpt.write(f"Translate Subs Report: {folder}\n")
        rpt.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        rpt.write(f"{'=' * 60}\n\n")

        if has_target:
            rpt.write(f"ALREADY HAS NORWEGIAN ({len(has_target)} files):\n")
            for f in sorted(has_target):
                rpt.write(f"  {f}\n")
            rpt.write("\n")

        translated_files = stats.get("translated_files", [])
        if translated_files:
            rpt.write(f"TRANSLATED ({len(translated_files)} files):\n")
            for f in sorted(translated_files):
                rpt.write(f"  {f}\n")
            rpt.write("\n")

        if no_subs:
            rpt.write(f"NO ELIGIBLE SUBTITLES ({len(no_subs)} files):\n")
            rpt.write("  These files need subtitles acquired manually.\n")
            for f in sorted(no_subs):
                rpt.write(f"  {f}\n")
            rpt.write("\n")

        rpt.write(f"SUMMARY:\n")
        rpt.write(f"  Total: {stats['total']} | Already done: {len(has_target)}")
        rpt.write(f" | Translated: {stats['translated']} | No subs: {len(no_subs)}")
        rpt.write(f" | Errors: {stats['errors']}\n")
        rpt.write(f"  Time: {elapsed:.1f}s\n")

    log.info("Report saved: %s", report_path)


if __name__ == "__main__":
    main()
