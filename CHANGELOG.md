# Changelog

## Unreleased

### Features

- **Configurable PGS/bitmap subtitle removal** — new `remove_bitmap_subs` setting in `llm_config.json`. When enabled (default for our setup), PGS and DVD subtitle tracks are always removed and ignored when checking for existing target language subs. Text-based subs (SRT) are preferred for translation. Set to `false` to keep PGS tracks.
- **DeepSeek recommended** as default provider in README with real-world cost breakdown (~1 cent per episode).

### Changes

- **`llm_config.json` is now gitignored** — local settings are no longer overwritten by `git pull`. New installs get `llm_config.example.json` copied automatically, with `remove_bitmap_subs: false` as the safe default.

---

## v1.1.2 — 2026-04-04

### Features

- **Fallback translation from any language** — when no subtitle in the priority source list is found, the script now falls back to any available subtitle in any language (embedded or external). The LLM can translate from virtually any language. Fallback usage is logged as `[FALLBACK]` for easy review.
- **Update scripts** — `update.ps1` (Windows) and `linux/update.sh` (Linux) handle pulling the latest version, stashing local changes, and updating Python packages.

### Bug Fixes

- **Fixed ffmpeg stealing terminal input** — added `-nostdin` to all ffmpeg calls, preventing the terminal from becoming unresponsive after the script finishes.

### Documentation

- Rewrote README for clarity and readability.
- Documented supported file formats (MKV gets full embed+clean, other formats get external `.srt` files).
- Clarified that mux and clean run automatically — standalone scripts are optional.
- Replaced technical jargon ("sidecar") with plain language ("external subtitle file").
- Added usage disclaimer covering file modification risks, API costs, and legal responsibility.

---

## v1.1.1 — 2026-04-04

### Bug Fixes

- **Fixed sidecar detection missing `.sdh`, `.hi`, `.cc`, `.forced` variants** — sidecars like `Episode.en.sdh.srt` were not found during scanning and silently deleted during cleanup. Now correctly detected, translated, and muxed in.
- **Don't delete unclassified sidecars** — sidecars whose language can't be determined are now left alone instead of being deleted. Only sidecars that were muxed in, are redundant, or are in an unwanted language get removed.
- **Fixed subtitle track titles** — retagged tracks (nob→nor) and muxed sidecars now get clean titles ("Norwegian", "English", etc.) instead of inheriting stale titles like "Norwegian Bokmal".
- **Fixed pick.sh on Linux** — replaced Unicode box-drawing characters with ASCII, added `.gitattributes` to force LF line endings for shell scripts, marked all `.sh` files executable in git.

### Improvements

- **pick.sh reads paths from `media_roots.conf`** — media folder paths are now stored in a gitignored config file so `git pull` doesn't overwrite your settings.
- **Improved `keep_with` documentation** in README with clear explanation and examples.

---

## v1.1.0 — 2026-04-04

### Features

- **Linux/Debian support** — full set of bash wrappers in `linux/` for all scripts, plus `install.sh` for Debian 13 (apt-get, Python venv, ffmpeg, mkvtoolnix).
- **Streaming pipeline** — translation starts as soon as the first file is found, instead of waiting for the entire folder scan to complete. Major perceived speedup on network/VPN paths.
- **Directory cache** — one `rglob` pass at startup replaces thousands of per-file `exists()` calls. Eliminates slow sidecar lookups over network shares.
- **Combined mux+clean** — single ffmpeg remux pass that adds translated subs, muxes in wanted sidecars (no/en/da/sv), removes unwanted tracks, and deletes all sidecar files. Halves network I/O compared to the previous two-pass approach.
- **Sidecar consolidation** — all wanted-language sidecars (Norwegian, English, Danish, Swedish) are muxed into the MKV in one pass. All sidecar files are deleted after processing.
- **Automatic nob→nor retag** — embedded tracks tagged "nob" (Norwegian Bokmål) are automatically retagged to "nor" (Norwegian) during the remux pass, with zero overhead.
- **Fuzzy folder picker** (`linux/pick.sh`) — fzf-based interactive selector for choosing a media folder and action (translate, clean, mux, dry-run). No more typing long paths.
- **sync-folder** script (`.ps1` and `.sh`) — sync video files between local and remote folders with date comparison, delete-before-copy to avoid SMB permission errors.
- **start-llama-server** script (`.ps1` and `.sh`) — launch llama.cpp server for local LLM inference.
- **Per-profile settings** — `batch_size`, `parallel`, and `timeout` can now be configured per LLM profile in `llm_config.json`.
- **Parallel default bumped to 8** for cloud API profiles (DeepSeek, OpenAI, etc.).

### Bug Fixes

- Fixed `resolve_profile()` stripping `timeout`, `batch_size`, and `parallel` from profile config.
- Fixed PS1 wrappers overriding profile-based `batch_size`/`parallel` defaults when not explicitly specified.

---

## v1.0.0 — 2026-03-22

First public release.

### Features

- **Configurable target language** — translate subtitles to any language by editing `llm_config.json`. Ships with Norwegian Bokmål as default; examples included for French, German, and Brazilian Portuguese.
- **Multiple LLM providers** — supports DeepSeek, OpenAI, Groq, Mistral, OpenRouter, Ollama, and LM Studio out of the box. Any OpenAI-compatible API can be added as a custom profile.
- **9 source languages** — translates from English, Danish, Swedish, German, Dutch, French, Spanish, Portuguese, and Italian. Priority order is configurable.
- **Works with any media** — TV series, movies, documentaries. Recursively scans any folder structure.
- **Supported formats** — MKV, MP4, AVI, MOV, WebM, OGM. For MKV files, translated subtitles are muxed in as embedded tracks. For other formats, a sidecar `.srt` file is created.
- **Per-file workflow** — each file goes through translate, mux, and clean before moving to the next, keeping disk usage predictable.
- **Parallel translation** — process multiple files concurrently (default 3).
- **Untagged track detection** — subtitle tracks with no language tag are identified via LLM and tagged in the MKV.
- **Subtitle cleaning** — removes unwanted subtitle tracks from MKVs, keeping only target language and configured extras.
- **Idempotent** — safe to re-run at any time. Files with existing translations are skipped.
- **Dry-run mode** — preview what would happen without modifying files or calling APIs.
- **One-liner install** — `irm https://raw.githubusercontent.com/dexusno/Translate_Subs/main/install.ps1 | iex` handles cloning, Python setup, and configuration.
- **UNC path support** — works with network shares (`\\nas\media\...`).

### Bug Fixes

- Fixed `--dry-run` in mux_subs deleting sidecar files when target language was already embedded.
- Fixed `UnicodeDecodeError` crash on Windows when ffprobe output contains non-ASCII metadata (e.g. special characters in track titles). All subprocess calls now use UTF-8 encoding.
