# Translate_Subs

> Batch-translate subtitles for your entire media library using LLMs that understand **context, idioms, and slang** — not just word-for-word replacement.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue)](https://github.com/dexusno/Translate_Subs)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

Point it at a folder — movies, TV series, documentaries, entire libraries — and it handles everything. Detects existing subtitles (embedded or external), translates them into your language, embeds them into your MKVs, and cleans up what you don't need. All in one pass.

The result is subtitles that read like they were written by a native speaker, at a cost of roughly **1 cent per episode**.

---

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Cost](#cost)
- [Installation](#installation)
  - [Windows](#windows-install)
  - [Linux (Debian/Ubuntu)](#linux-install)
- [Configuration](#configuration)
  - [Target Language](#target-language)
  - [Keeping Other Languages](#keeping-other-languages)
  - [Source Languages](#source-languages)
  - [LLM Profiles](#llm-profiles)
  - [Per-Profile Tuning](#per-profile-tuning)
  - [Bitmap Subtitle Removal](#bitmap-subtitle-removal-pgs)
- [Usage](#usage)
- [Scripts](#scripts)
- [Supported File Formats](#supported-file-formats)
- [Troubleshooting](#troubleshooting)
- [Disclaimer](#disclaimer)
- [License](#license)

---

## Features

- **Natural translations** — LLMs understand context, tone, and intent. Jokes land, slang makes sense, dialogue flows naturally.
- **Fully configurable** — target language, source priority list, which languages to keep, which LLM to use. All in one config file.
- **Hands-off batch processing** — point at a folder and walk away. Already translated files are skipped. Safe to re-run any time.
- **Fast** — streaming pipeline starts translating as soon as the first file is found. Up to 8 files translated in parallel.
- **Network-friendly** — directory caching eliminates thousands of slow lookups over VPN/network shares.
- **Flexible source detection** — finds subtitles in external files (`.srt`, `.ass`, including `.sdh`/`.hi`/`.forced` variants), embedded MKV tracks, and even untagged tracks (identified via LLM).
- **Smart fallback** — if no preferred-language subtitle is available, falls back to any language it can find. The LLM handles the rest.
- **One-pass remux** — translating, embedding, and cleaning happen in a single ffmpeg call. Half the I/O of doing them separately.
- **Self-healing translations** — detects when the LLM stops mid-batch and automatically retries only the failed portion.
- **Multiple LLM providers** — DeepSeek, OpenAI, Groq, Mistral, OpenRouter, Ollama, LM Studio. Any OpenAI-compatible API works.
- **Cross-platform** — PowerShell wrappers for Windows, Bash wrappers for Linux. Same Python core on both.

---

## How It Works

```
Video file
  │
  ▼
[1] Skip check ─── already has target language? skip
  │
  ▼
[2] Source detection ─── find subtitles in preferred languages
  │                      (external .srt/.ass first, then embedded)
  ▼
[3] Fallback ─── if nothing preferred, use ANY available language
  │
  ▼
[4] LLM Translation ─── batch translate via DeepSeek/OpenAI/etc.
  │                     with self-healing retry on failures
  ▼
[5] Reflow ─── ensure max 2 lines per subtitle cue
  │
  ▼
[6] MKV remux ─── embed translated subs + keep wanted tracks +
  │               drop unwanted tracks, all in ONE pass
  ▼
Finished MKV with only your wanted languages
```

**What it does:**
- Scans a folder recursively for video files (MKV, MP4, AVI, MOV, WebM, OGM)
- Skips any video that already has the target language (embedded or external)
- Extracts and translates the best available source subtitles
- Embeds the translated output back into MKV files as a native subtitle track
- Strips any unwanted languages to keep your files clean
- Never touches the original video or audio streams

Translation and scanning overlap — the first file starts translating while the rest are still being scanned.

---

## Cost

| Scale | Approximate cost |
|---|---|
| 1 episode (45 min) | ~$0.01 |
| 1 season (10 episodes) | ~$0.10 |
| 1 full series (50 episodes) | ~$0.50 |
| 100 episodes | ~$1 |
| 1,000 episodes | ~$10 |

> [!NOTE]
> These estimates are based on **DeepSeek Chat pricing as of April 2026** (the default and recommended provider). A cup of coffee pays for ~500 episodes. A Netflix monthly subscription covers ~1,500. Other providers (OpenAI, Anthropic, etc.) can cost 10-50x more for similar quality. Always check your provider's current pricing before processing large libraries.

> The `--dry-run` flag lets you preview exactly what would be processed without making any API calls or modifying any files.

---

## Installation

### Requirements (both platforms)

| Component | Required | Notes |
|---|---|---|
| **Python 3.11+** | Yes | Installed by the script if missing |
| **Git** | Yes | Installed by the script if missing |
| **FFmpeg + FFprobe** | Yes | Installed by the script if missing |
| **LLM API key** | Yes | DeepSeek recommended (tested extensively). Other OpenAI-compatible providers also work |

---

### Windows Install

#### Prerequisites
- Windows 10 or 11
- PowerShell 5.1+ (built into Windows)
- Python 3.11+ (the installer can install it for you)

#### Step 1: Run the installer

Open PowerShell, navigate to where you want to install, and run the one-liner:

```powershell
cd D:\
irm https://raw.githubusercontent.com/dexusno/Translate_Subs/main/install.ps1 | iex
```

This creates a `Translate_Subs` folder in your current directory (e.g. `D:\Translate_Subs`).

The installer will:
1. Check for Python 3.11+, git, ffmpeg — offer to install via `winget` if missing
2. Clone the repository
3. Install Python dependencies (`requests`, `python-dotenv`)
4. Create `.env` and `llm_config.json` from templates

#### Step 2: Get your LLM API key

DeepSeek is recommended (see [Configuration](#configuration) below), but any OpenAI-compatible provider works.

#### Step 3: Run it

```powershell
cd D:\Translate_Subs
.\translate_subs.ps1 "D:\Movies\Some Movie"
```

---

### Linux Install

#### Prerequisites
- Debian 13, Ubuntu 22.04+, or similar
- `sudo` access (for installing system packages)

#### Step 1: Run the installer

Open a terminal, navigate to where you want to install, and run the one-liner:

```bash
cd /opt
curl -fsSL https://raw.githubusercontent.com/dexusno/Translate_Subs/main/linux/install.sh | bash
```

This creates a `Translate_Subs` folder in your current directory (e.g. `/opt/Translate_Subs`).

The installer will:
1. `apt-get install` system dependencies (python3, python3-venv, git, ffmpeg, mkvtoolnix)
2. Clone the repository
3. Create a Python `venv` at `.venv/` inside the project
4. Install all Python dependencies
5. Create `.env` and `llm_config.json` from templates
6. Mark all shell scripts as executable

#### Step 2: Get your LLM API key

DeepSeek is recommended (see [Configuration](#configuration) below), but any OpenAI-compatible provider works.

#### Step 3: Run it

```bash
cd /opt/Translate_Subs
./linux/translate_subs.sh "/media/tv/Some Show"
```

The wrapper runs the venv Python directly — no manual activation needed.

#### Updating

On either platform, to pull the latest version while keeping your local config:

```bash
./linux/update.sh        # Linux
.\update.ps1             # Windows
```

This stashes any local changes, pulls the latest commits, then restores your modifications.

---

## Configuration

All configuration lives in `llm_config.json` in the project directory. On first install, it's created automatically from `llm_config.example.json`.

> [!IMPORTANT]
> `llm_config.json` is gitignored — your settings survive updates. Never edit `llm_config.example.json` for your personal config.

Full file structure at a glance:

```
llm_config.json
├── default_profile         Which LLM provider to use (e.g. "deepseek")
├── remove_bitmap_subs      Remove PGS/DVD bitmap subtitle tracks (true/false)
├── target_language
│   ├── name                Language name for the LLM prompt
│   ├── codes               ISO codes that mean "already translated, skip"
│   ├── sidecar_code        Output filename code (Movie.XX.srt)
│   ├── mkv_tag             Language tag when embedding into MKV
│   └── keep_with           Other languages to keep alongside the target
├── source_languages        Ordered list of languages to translate FROM
└── profiles                LLM provider configs (API URL, model, key, tuning)
```

Each section is explained below.

### Target Language

The `target_language` block defines what you're translating **to** and which ISO codes represent it:

```json
"target_language": {
  "name": "Norwegian",
  "codes": ["no", "nor", "nob", "nb", "nno"],
  "sidecar_code": "no",
  "mkv_tag": "nor",
  "keep_with": ["en", "eng", "da", "dan", "sv", "swe"]
}
```

| Field | What it does |
|---|---|
| `name` | The language name sent to the LLM in the translation prompt. |
| `codes` | All ISO codes that represent this language. If a file already has subtitles tagged with any of these codes, it's considered done and skipped. |
| `sidecar_code` | The code used in output filenames: `Movie.{code}.srt` |
| `mkv_tag` | The language tag applied when embedding translated subs into an MKV. |
| `keep_with` | Languages to keep alongside your target (see below). |

<details>
<summary><strong>Examples for other target languages</strong></summary>

**French** (keep English alongside):
```json
"target_language": {
  "name": "French",
  "codes": ["fr", "fra", "fre"],
  "sidecar_code": "fr",
  "mkv_tag": "fra",
  "keep_with": ["en", "eng"]
}
```

**German** (keep English alongside):
```json
"target_language": {
  "name": "German",
  "codes": ["de", "deu", "ger"],
  "sidecar_code": "de",
  "mkv_tag": "deu",
  "keep_with": ["en", "eng"]
}
```

**Brazilian Portuguese** (keep English and Spanish):
```json
"target_language": {
  "name": "Brazilian Portuguese",
  "codes": ["pt", "por"],
  "sidecar_code": "pt",
  "mkv_tag": "por",
  "keep_with": ["en", "eng", "es", "spa"]
}
```
</details>

### Keeping Other Languages

`keep_with` controls which additional languages are allowed to remain in your MKV files. Your target language is always kept — you don't need to list it here.

This setting affects two things:

- **Embedded subtitle tracks** — tracks tagged with a `keep_with` language stay in the MKV. Tracks in any other language are removed.
- **External subtitle files** — if an external `.srt` or `.ass` file exists for a `keep_with` language and it isn't already embedded, it gets embedded automatically. After processing, all recognized external subtitle files are cleaned up.

After processing, each MKV will contain only your target language and the languages listed in `keep_with`. Everything else is stripped out.

### Source Languages

The `source_languages` list controls which subtitle tracks are preferred as a translation source. Languages are tried in priority order — the first match wins:

```json
"source_languages": [
  {"codes": ["en", "eng"], "name": "English"},
  {"codes": ["da", "dan"], "name": "Danish"},
  {"codes": ["sv", "swe"], "name": "Swedish"}
]
```

Add, remove, or reorder languages to match your library. If none of these are found, the script falls back to any available subtitle in any language.

### LLM Profiles

Choose your translation backend with `--profile`. Each profile is defined in `llm_config.json`:

| Profile | Provider | Model | Notes |
|---|---|---|---|
| `deepseek` | DeepSeek | deepseek-chat | **Recommended** — excellent quality, very low cost |
| `openai` | OpenAI | gpt-4o | High quality, significantly higher cost |
| `groq` | Groq | llama-3.3-70b | Free tier available |
| `mistral` | Mistral | mistral-large | Good for European languages |
| `openrouter` | OpenRouter | deepseek/deepseek-chat | Access to many models via one API |
| `local` | Ollama / LM Studio | (loaded model) | Free, runs locally |

> **We recommend DeepSeek** as the default. It produces natural, context-aware translations at a fraction of the cost of other cloud APIs. See [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) for current rates.
>
> *We have no affiliation with DeepSeek and receive no benefit from recommending them — it's simply what works best for this use case.*

<details>
<summary><strong>Adding a custom provider</strong></summary>

Any OpenAI-compatible API works:

```json
"my-provider": {
  "api_url": "https://api.example.com/v1/chat/completions",
  "model": "model-name",
  "api_key_env": "MY_PROVIDER_API_KEY"
}
```

Then add `MY_PROVIDER_API_KEY=your-key` to `.env`. For local models that don't need a key, use `"api_key": "none"` instead.
</details>

### Per-Profile Tuning

Each profile can include performance settings. Cloud APIs handle larger batches efficiently, while local models benefit from smaller batches and longer timeouts:

```json
"deepseek": {
  "batch_size": 200,
  "parallel": 8
},
"local": {
  "batch_size": 25,
  "parallel": 1,
  "timeout": 600
}
```

| Setting | What it does | Cloud default | Local default |
|---|---|---|---|
| `batch_size` | Subtitle groups per API call | 200 | 25 |
| `parallel` | Files translated concurrently | 8 | 1 |
| `timeout` | Seconds before an API call times out | 120 | 600 |

CLI flags (`--batch-size`, `--parallel`) override profile settings when specified.

> [!NOTE]
> The cloud `batch_size` of 200 is tuned for DeepSeek's 8K output token limit. Raising it further may cause mid-batch truncation (the script detects and retries this, but it's slower). Lowering it is always safe.

### Bitmap Subtitle Removal (PGS)

Bitmap-based subtitle tracks (PGS, DVD subs) can optionally be removed during cleaning. These formats are incompatible with many players and workflows, and text-based subtitles (SRT) are generally preferred.

```json
"remove_bitmap_subs": true
```

When enabled, PGS and DVD subtitle tracks are removed regardless of language, and are ignored when checking for existing target language subs. If a file has target-language PGS subs but also has text-based subs in another language, the script will translate the text subs instead.

> **Default: `false`** (PGS tracks are kept). Set to `true` if you want them removed.

---

## Usage

### Interactive folder picker (Linux)

Browse your media library and pick a folder without typing paths:

```bash
sudo apt install fzf          # one-time setup
./linux/pick.sh                # pick from your configured media roots
./linux/pick.sh /mnt/media/Tv  # or pick from a specific folder
```

Type a few letters to filter, arrow keys to select, then choose an action (translate, clean, mux, or dry-run variants). Media folder paths are stored in `media_roots.conf` (gitignored — survives `git pull`).

### Windows

```powershell
# Basic — translate everything in a folder
.\translate_subs.ps1 "D:\Movies\Inception (2010)"

# Entire TV series (all seasons)
.\translate_subs.ps1 "D:\TvSeries\Breaking Bad"

# Preview — see what would be translated
.\translate_subs.ps1 "D:\Media" -DryRun

# Different LLM provider
.\translate_subs.ps1 "D:\Movies" -Profile openai

# Limit files, retranslate existing, keep external files
.\translate_subs.ps1 "D:\Movies" -Limit 5 -Force -KeepSidecar

# Network share (UNC paths supported)
.\translate_subs.ps1 "\\nas\media\Movies"
```

### Linux

```bash
# Basic — translate everything in a folder
./linux/translate_subs.sh "/media/movies/Inception (2010)"

# Entire TV series (all seasons)
./linux/translate_subs.sh "/media/tv/Breaking Bad"

# Preview — see what would be translated
./linux/translate_subs.sh "/media" --dry-run

# Different LLM provider
./linux/translate_subs.sh "/media/movies" --profile openai

# Limit files, retranslate existing, keep external files
./linux/translate_subs.sh "/media/movies" --limit 5 --force --keep-sidecar

# Network share (mounted via SMB/NFS)
./linux/translate_subs.sh "/mnt/nas/movies"
```

### CLI Options

| Windows flag | Linux flag | Description | Default |
|---|---|---|---|
| `folder` | `folder` | Path to scan for video files | Required |
| `-Profile` | `--profile` | LLM profile from llm_config.json | `deepseek` |
| `-BatchSize` | `--batch-size` | Subtitle groups per LLM API call | 200 |
| `-Parallel` | `--parallel` | Concurrent file processing | 8 |
| `-Limit` | `--limit` | Max number of files to process | unlimited |
| `-Force` | `--force` | Retranslate even if target exists | off |
| `-DryRun` | `--dry-run` | Preview without making changes | off |
| `-KeepSidecar` | `--keep-sidecar` | Keep external `.srt` after muxing | off |
| `-SkipClean` | `--skip-clean` | Don't strip unwanted tracks | off |
| `-SkipDetect` | `--skip-detect` | Don't detect untagged subtitle languages | off |
| `-LogFile` | `--log-file` | Also write log output to this file | none |

### Standalone tools

The main `translate_subs` script handles everything automatically — translating, embedding into MKV, and cleaning unwanted tracks in one pass. **You don't need to run the tools below separately under normal use.**

They're available as standalone scripts if you want to run just one step on its own:

<details>
<summary><strong>Embed external subtitles into MKVs</strong></summary>

Useful if you already have `.srt` files from another source and just want to embed them:

```powershell
.\mux_subs.ps1 "D:\TvSeries\Show"                 # Windows
```
```bash
./linux/mux_subs.sh "/media/tv/Show"               # Linux
```
</details>

<details>
<summary><strong>Clean unwanted subtitle tracks</strong></summary>

Useful if you just want to strip unwanted languages without translating:

```powershell
.\clean_subs.ps1 "D:\Movies" -DryRun               # Windows
```
```bash
./linux/clean_subs.sh "/media/movies" --dry-run    # Linux
```
</details>

---

## Scripts

| PowerShell | Linux | Purpose |
|---|---|---|
| `translate_subs.ps1` / `.py` | `linux/translate_subs.sh` | Translate, embed, and clean in one pass |
| `mux_subs.ps1` / `.py` | `linux/mux_subs.sh` | Embed external subtitle files into MKV containers |
| `clean_subs.ps1` / `.py` | `linux/clean_subs.sh` | Remove unwanted subtitle tracks from MKVs |
| `start-llama-server.ps1` | `linux/start-llama-server.sh` | Start llama.cpp server for local translation |
| `install.ps1` | `linux/install.sh` | Install dependencies and configure the project |
| `update.ps1` | `linux/update.sh` | Update to the latest version, preserving local changes |
| — | `linux/pick.sh` | Interactive folder picker (requires `fzf`) |

---

## Supported File Formats

The script works with any common video format — **MKV is not required**.

| Format | Translation | Embedding | Track cleanup |
|---|---|---|---|
| **MKV** | Full support — translates from embedded or external subtitles | Translated subs + wanted external files are embedded directly into the MKV | Unwanted subtitle tracks are removed, external files cleaned up |
| **MP4, AVI, MOV, WebM, OGM** | Full support — translates from external `.srt`/`.ass` files | Not supported (these formats don't allow easy subtitle embedding without re-encoding) | Not applicable |

For non-MKV files, the translated subtitles are saved as a `.srt` file next to the video (e.g. `Movie.no.srt`). Existing external subtitle files are left untouched. Most players — Plex, Jellyfin, VLC, Kodi — pick up external `.srt` files automatically.

The scripts work with any folder layout. Point them at any level and they scan recursively:

```
Movies/
  Inception (2010)/
    Inception (2010).mkv

TvSeries/
  Breaking Bad/
    Season 01/
      Breaking.Bad.S01E01.mkv

Media/              # point here to process everything
  Movies/
  TvSeries/
  Documentaries/
```

---

## Troubleshooting

**"ffmpeg not found"**
Install ffmpeg and ensure both `ffmpeg` and `ffprobe` are on your PATH.
- Windows: `winget install ffmpeg` then restart your terminal
- Linux: `sudo apt-get install ffmpeg`

**"Python packages not found" (Linux)**
The Linux scripts use a virtual environment at `.venv/`. Re-run `./linux/install.sh` or manually install:
```bash
.venv/bin/pip install requests python-dotenv
```

**API timeout**
If translations time out on large files, reduce `--batch-size` (default 200) or increase the `timeout` in your profile config.

**Partially translated episodes**
If subtitles switch from your target language back to the source mid-episode, the batch size may be too large for your LLM provider's output token limit. DeepSeek `deepseek-chat` has an 8K output token limit — the default `batch_size` of 200 is tuned to stay within this. If you see `finish_reason=length` warnings, lower the batch size further. The script automatically detects and retries failed portions, but smaller batches prevent the issue entirely.

**Safe to re-run**
Already translated files are skipped. Partially translated files (interrupted mid-write) are retranslated. You can stop and resume at any time.

---

## Disclaimer

This software is provided as-is, without warranty of any kind. By using Translate_Subs, you acknowledge the following:

- **File modification** — this tool modifies media files in place (remuxing MKV containers, deleting external subtitle files). While it uses atomic file operations and creates backups during remuxing, data loss is always possible. **Back up your media library before running on important files.**
- **Translation quality** — translations are generated by third-party LLM APIs or local models. Output quality depends on the model, the source material, and the language pair. Always spot-check translations before relying on them.
- **API costs** — cloud LLM providers charge per token. While costs are low (~$0.01 per episode), processing a very large library will accumulate charges. Use `--dry-run` to preview what will be processed before committing.
- **Third-party services** — this tool sends subtitle text (not video) to external APIs (DeepSeek, OpenAI, etc.) for translation. Do not use it on content you are not authorized to share with these services.
- **Legal responsibility** — you are solely responsible for ensuring your use of this tool complies with applicable laws, including copyright and content licensing. The authors of this project are not responsible for how it is used.

---

## License

MIT
