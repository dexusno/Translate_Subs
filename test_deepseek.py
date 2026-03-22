"""
Test: translate test2.srt using the DeepSeek API.
Loads target language from llm_config.json (same config as translate_series.py).
"""

import sys
import os
import io
import json
import re
import time
import requests
from pathlib import Path
from typing import List, Dict, Callable, Optional
from dotenv import load_dotenv

# Force UTF-8 output on Windows
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout.reconfigure(encoding="utf-8")

# Try importing SRT utilities from translate_series (preferred) or app/srtxlate (legacy)
try:
    from translate_series import (
        _split_srt, _join_srt, _protect_tags, _restore_tags,
        _is_index_line, _is_time_line, _is_allcaps_marker,
        _nfc, _strip_bom, _split_to_n_lines_preserving_words,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent / "app"))
    from srtxlate import (
        _split_srt, _join_srt, _protect_tags, _restore_tags,
        _is_index_line, _is_time_line, _is_allcaps_marker,
        _nfc, _strip_bom, _split_to_n_lines_preserving_words,
    )

load_dotenv()

# Load target language from config
CONFIG_FILE = Path(__file__).parent / "llm_config.json"
_TARGET_NAME = "Norwegian Bokmål"
try:
    with open(CONFIG_FILE, encoding="utf-8") as _f:
        _config = json.load(_f)
    _TARGET_NAME = _config.get("target_language", {}).get("name", _TARGET_NAME)
except (FileNotFoundError, json.JSONDecodeError):
    pass

API_KEY = os.getenv("DEEPSEEK_API_KEY")
API_URL = "https://api.deepseek.com/v1/chat/completions"
SRT_FILE = Path(__file__).parent / "test2.srt"

_SENTINEL = "__NL__"

SYSTEM_PROMPT = (
    "You are a professional subtitle translator. "
    f"Translate the following English subtitle lines to {_TARGET_NAME}. "
    "Each line is prefixed with [N] where N is the line number. "
    "Return each translated line prefixed with the SAME [N] marker. "
    "Preserve any __NL__ markers exactly as they appear — do not translate or remove them. "
    "Preserve any __TAG0__, __TAG1__ etc. placeholders exactly. "
    "Keep sound effects in ALL CAPS (e.g. ENGINE ROARS → MOTOREN BRØLER). "
    "Do not add any explanations, only the numbered translated lines."
)


def _deepseek_translate_batched(
    lines: List[str],
    source: str,
    target: str,
    batch_size: int = 30,
    glossary: Optional[Dict[str, str]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> List[str]:
    """Translate lines via DeepSeek, using same interface as _nllb_translate_batched."""
    if not lines:
        return []

    glossary = glossary or {}

    # Protect tags + glossary substitutions (same as srtxlate.py)
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

    for i in range(0, total, max(1, batch_size)):
        batch = prepped[i : i + batch_size]

        # Build numbered input
        numbered = [f"[{j}] {line}" for j, line in enumerate(batch)]
        user_msg = "\n".join(numbered)

        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
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

        # Build ordered output for this batch
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

    print(f"\n--- DeepSeek API Usage ---")
    print(f"  Prompt tokens:     {total_usage['prompt_tokens']}")
    print(f"  Completion tokens: {total_usage['completion_tokens']}")
    print(f"  Total tokens:      {total_usage['total_tokens']}")

    return restored


def translate_srt_deepseek(srt_text: str, batch_size: int = 30, progress_cb: Optional[Callable[[int, int], None]] = None) -> str:
    """
    Full SRT translation using srtxlate's parsing logic + DeepSeek backend.
    This mirrors translate_srt_with_progress() from srtxlate.py.
    """
    blocks = _split_srt(srt_text)

    # Build groups to translate (same logic as srtxlate.py)
    groups: List[str] = []
    placements = []

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

        # Partition: marker lines stand alone, others merge with sentinel
        run = []
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

    glossary = {
        "removal men": "movers",
        "removals men": "movers",
    }

    translated = _deepseek_translate_batched(
        groups, "eng_Latn", "nob_Latn",
        batch_size=batch_size, glossary=glossary,
        progress_cb=progress_cb,
    )

    # Place translated strings back (same logic as srtxlate.py)
    gi = 0
    for (bi, idxs) in placements:
        text = _nfc(translated[gi] if gi < len(translated) else "")
        gi += 1

        if len(idxs) == 1:
            blocks[bi][idxs[0]] = text.strip()
            continue

        # Multi-line: split by sentinel first
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


def main():
    if not API_KEY:
        print("ERROR: DEEPSEEK_API_KEY not set in .env")
        return

    print(f"Loading: {SRT_FILE}")
    with open(SRT_FILE, "r", encoding="utf-8-sig") as f:
        srt_text = f.read()

    print("Translating with DeepSeek using srtxlate pipeline...\n")

    start = time.time()
    result = translate_srt_deepseek(srt_text)
    elapsed = time.time() - start

    # Write output file
    out_path = SRT_FILE.with_suffix(".nob.srt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"\n{'='*80}")
    print(f"  DONE in {elapsed:.1f}s")
    print(f"  Output: {out_path}")
    print(f"{'='*80}\n")

    # Show first 30 blocks side-by-side for review
    orig_blocks = _split_srt(srt_text)
    trans_blocks = _split_srt(result)

    shown = 0
    for ob, tb in zip(orig_blocks, trans_blocks):
        if shown >= 30:
            break
        # Find text lines
        o_text = [l for l in ob if not _is_index_line(l) and not _is_time_line(l) and l.strip()]
        t_text = [l for l in tb if not _is_index_line(l) and not _is_time_line(l) and l.strip()]
        if not o_text:
            continue
        # Get index and time
        idx = next((l for l in ob if _is_index_line(l)), "?")
        time_line = next((l for l in ob if _is_time_line(l)), "")
        print(f"#{idx.strip()}  {time_line.strip()}")
        print(f"  EN: {' / '.join(o_text)}")
        print(f"  NO: {' / '.join(t_text)}")
        print()
        shown += 1


if __name__ == "__main__":
    main()
