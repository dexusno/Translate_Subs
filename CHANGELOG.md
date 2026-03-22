# Changelog

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
