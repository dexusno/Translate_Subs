# Translate Subs

Batch-translate subtitles to any target language using LLM APIs. Scans media folders recursively, finds subtitles in any supported source language (embedded or sidecar), translates them, and optionally muxes the result back into MKV containers.

Supports multiple LLM providers out of the box: DeepSeek, OpenAI, Groq, Mistral, OpenRouter, Ollama, and LM Studio.

## Features

- **Configurable target language** — translate to Norwegian, French, German, or any language
- Translates from English, Danish, Swedish, German, Dutch, French, Spanish, Portuguese, and Italian (configurable)
- Detects untagged subtitle tracks via LLM and tags them in the MKV
- Muxes translated sidecar files into MKV containers as embedded tracks
- Cleans unwanted subtitle tracks from MKVs (keeps target language + configured extras)
- Per-file workflow: translate, mux, clean — each file is fully processed before moving to the next
- Parallel translation (configurable, default 3 concurrent files)
- Idempotent — re-running skips already translated files
- Dry-run mode for previewing what would happen

## Requirements

- **Python 3.11+** with `requests` and `python-dotenv`
- **ffmpeg** and **ffprobe** on PATH
- An API key for at least one LLM provider (or a local model via Ollama/LM Studio)

## Quick Start

### One-liner Install (Windows PowerShell)

```powershell
irm https://raw.githubusercontent.com/dexusno/Translate_Subs/main/install.ps1 | iex
```

This will clone the repo, install Python packages, and set up your `.env` file.

### Manual Install

```powershell
git clone https://github.com/dexusno/Translate_Subs.git
cd Translate_Subs
pip install requests python-dotenv
cp .env.example .env
# Edit .env and add your API key
```

### Verify

```powershell
.\translate_series.ps1 "D:\TvSeries\Some Show" -DryRun
```

## Configuration

### Target Language

Set the target language in `llm_config.json`:

```json
"target_language": {
  "name": "Norwegian Bokmål",
  "codes": ["no", "nor", "nob", "nb", "nno"],
  "sidecar_code": "no",
  "mkv_tag": "nob",
  "keep_with": ["en", "eng", "da", "dan", "sv", "swe"]
}
```

| Field | Purpose | Example |
|-------|---------|---------|
| `name` | Used in the LLM translation prompt | `"Norwegian Bokmål"` |
| `codes` | ISO codes that mean "target already exists" (skip check) | `["no", "nor", "nob"]` |
| `sidecar_code` | Output filename: `Episode.{code}.srt` | `"no"` → `Episode.no.srt` |
| `mkv_tag` | Language tag when muxing into MKV | `"nob"` |
| `keep_with` | Additional languages to keep when cleaning (target is always kept) | `["en", "eng"]` |

#### Examples for other languages

**French:**
```json
"target_language": {
  "name": "French",
  "codes": ["fr", "fra", "fre"],
  "sidecar_code": "fr",
  "mkv_tag": "fra",
  "keep_with": ["en", "eng"]
}
```

**German:**
```json
"target_language": {
  "name": "German",
  "codes": ["de", "deu", "ger"],
  "sidecar_code": "de",
  "mkv_tag": "deu",
  "keep_with": ["en", "eng"]
}
```

**Brazilian Portuguese:**
```json
"target_language": {
  "name": "Brazilian Portuguese",
  "codes": ["pt", "por"],
  "sidecar_code": "pt",
  "mkv_tag": "por",
  "keep_with": ["en", "eng", "es", "spa"]
}
```

### Source Languages

The `source_languages` list in `llm_config.json` controls which subtitle tracks can be used as translation source. Languages are tried in priority order — first match wins.

### LLM Profiles

Select a provider with `-Profile`. Profiles are defined in `llm_config.json`:

| Profile | Provider | Default Model | Notes |
|---------|----------|---------------|-------|
| `deepseek` | DeepSeek | deepseek-chat | Cheapest cloud option |
| `openai` | OpenAI | gpt-4o | High quality |
| `groq` | Groq | llama-3.3-70b | Free tier available |
| `mistral` | Mistral | mistral-large | Good European option |
| `openrouter` | OpenRouter | deepseek/deepseek-chat | Access to many models |
| `ollama` | Ollama | qwen2.5:14b | Free, runs locally |
| `lmstudio` | LM Studio | (loaded model) | Free, runs locally |

Any provider with an OpenAI-compatible API can be added:

```json
"my-provider": {
  "api_url": "https://api.example.com/v1/chat/completions",
  "model": "model-name",
  "api_key_env": "MY_PROVIDER_API_KEY"
}
```

Then add `MY_PROVIDER_API_KEY=your-key` to `.env`.

For local models that don't need a key, use `"api_key": "none"` instead of `"api_key_env"`.

## Usage

### Translate a folder

```powershell
# Translate using default profile (DeepSeek)
.\translate_series.ps1 "D:\TvSeries\Breaking Bad"

# Use a different LLM provider
.\translate_series.ps1 "D:\TvSeries\Show" -Profile openai

# Preview what would be translated
.\translate_series.ps1 "D:\TvSeries\Show" -DryRun

# Limit to 5 files (useful for testing)
.\translate_series.ps1 "D:\TvSeries\Show" -Limit 5

# Retranslate files that already have target subs
.\translate_series.ps1 "D:\TvSeries\Show" -Force

# Keep sidecar files after muxing (for spot-checking translations)
.\translate_series.ps1 "D:\TvSeries\Show" -KeepSidecar

# Skip cleaning unwanted subtitle tracks
.\translate_series.ps1 "D:\TvSeries\Show" -SkipClean

# Write log output to a file
.\translate_series.ps1 "D:\TvSeries\Show" -LogFile "C:\logs\translate.log"

# UNC paths (network shares) are supported
.\translate_series.ps1 "\\nas\media\Tv\Some Show"
```

### Mux existing sidecar files into MKVs

For folders that already have translated sidecar files from a previous run:

```powershell
.\mux_subs.ps1 "D:\TvSeries\Some Show"
.\mux_subs.ps1 "D:\TvSeries\Some Show" -KeepSidecar
.\mux_subs.ps1 "D:\TvSeries\Some Show" -DryRun
```

### Clean unwanted subtitle tracks

Remove subtitle tracks that aren't in the keep list:

```powershell
.\clean_subs.ps1 "D:\TvSeries\Some Show"
.\clean_subs.ps1 "D:\TvSeries\Some Show" -DryRun
```

## Scripts

| Script | Purpose |
|--------|---------|
| `translate_series.ps1` / `.py` | Main script — translate, mux, and clean in one pass |
| `mux_subs.ps1` / `.py` | Mux sidecar subtitles into MKV containers |
| `clean_subs.ps1` / `.py` | Remove unwanted subtitle tracks from MKVs |
| `test_deepseek.py` | Standalone test for the translation API |

## How It Works

For each video file found:

1. **Check if already done** — skip if target language subs exist (embedded or sidecar)
2. **Find source subtitles** — try sidecar files first, then embedded tracks, in language priority order
3. **Detect untagged tracks** — if an embedded track has no language tag, extract a sample and ask the LLM to identify it, then tag the track in the MKV
4. **Translate** — send subtitle text to the LLM in batches, write result as sidecar file
5. **Mux** — embed the sidecar into the MKV as a target language track
6. **Clean** — remove unwanted subtitle tracks (anything not in the keep list)

Files that are skipped (target already exists) still get a quick clean check to remove any unwanted tracks.

## Folder Structure

The scripts expect standard media folder structures:

```
TvSeries/
  Show Name/
    Season 01/
      Episode.S01E01.mkv
      Episode.S01E02.mkv

Movies/
  Movie Name (2024)/
    Movie Name (2024).mkv
```

## Troubleshooting

### ffmpeg not found

Install ffmpeg and ensure both `ffmpeg` and `ffprobe` are on your PATH:
- **Windows**: Download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or install with `winget install ffmpeg`
- **Linux**: `sudo apt install ffmpeg`

### API timeout

For very large batches, the LLM API call may time out. The default timeout is 120 seconds per request. If you hit timeouts, try reducing `--batch-size` (default 500).

### Re-running after interruption

Safe to re-run at any time. Files with target language subs already present are skipped. Partially translated files (interrupted mid-write) will be retranslated.

## License

MIT
