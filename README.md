# Translate Subs

Batch-translate subtitles to any target language using LLM APIs. Scans media folders recursively, finds subtitles in any supported source language (embedded or sidecar), translates them, and optionally muxes the result back into MKV containers.

Works with TV series, movies, documentaries, or any media library. Supports multiple LLM providers out of the box: DeepSeek, OpenAI, Groq, Mistral, OpenRouter, Ollama, and LM Studio.

## Features

- **Configurable target language** — translate to Norwegian, French, German, or any language
- Translates from English, Danish, Swedish, German, Dutch, French, Spanish, Portuguese, and Italian (configurable)
- **Works with any media** — TV series, movies, documentaries, any folder structure
- Supports MKV, MP4, AVI, MOV, WebM, and OGM video files
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

### Windows (PowerShell)

**One-liner install:**

```powershell
irm https://raw.githubusercontent.com/dexusno/Translate_Subs/main/install.ps1 | iex
```

**Manual install:**

```powershell
git clone https://github.com/dexusno/Translate_Subs.git
cd Translate_Subs
pip install requests python-dotenv
cp .env.example .env
# Edit .env and add your API key
```

**Verify:**

```powershell
.\translate_subs.ps1 "D:\Media\Some Folder" -DryRun
```

### Linux (Debian/Ubuntu)

**One-liner install:**

```bash
curl -fsSL https://raw.githubusercontent.com/dexusno/Translate_Subs/main/linux/install.sh | bash
```

**Manual install:**

```bash
sudo apt-get install python3 python3-pip python3-venv ffmpeg mkvtoolnix git
git clone https://github.com/dexusno/Translate_Subs.git
cd Translate_Subs
python3 -m venv .venv
.venv/bin/pip install requests python-dotenv
cp .env.example .env
# Edit .env and add your API key
```

**Verify:**

```bash
./linux/translate_subs.sh "/media/tv/Some Show" --dry-run
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
| `sidecar_code` | Output filename: `Movie.{code}.srt` | `"no"` → `Movie.no.srt` |
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

### Per-Profile Settings

Each profile can include `batch_size`, `parallel`, and `timeout` to tune performance. Cloud APIs handle large batches efficiently, while local models benefit from smaller batches and longer timeouts:

```json
"deepseek": {
  "api_url": "https://api.deepseek.com/v1/chat/completions",
  "model": "deepseek-chat",
  "api_key_env": "DEEPSEEK_API_KEY",
  "batch_size": 500,
  "parallel": 3
},
"lmstudio": {
  "api_url": "http://localhost:1234/v1/chat/completions",
  "model": "deepseek-r1-distill-qwen-14b",
  "api_key": "none",
  "timeout": 600,
  "batch_size": 100,
  "parallel": 1
}
```

| Setting | Description | Cloud default | Local default |
|---------|-------------|---------------|---------------|
| `batch_size` | Subtitle groups per API call | 500 | 100 |
| `parallel` | Concurrent files being translated | 3 | 1 |
| `timeout` | Seconds before API call times out | 120 | 600 |

CLI flags (`--batch-size`, `--parallel`) override profile settings when specified.

## Usage

### Interactive folder picker (Linux)

Instead of typing full paths, use the fuzzy picker to browse and select folders:

```bash
sudo apt install fzf          # one-time setup
./linux/pick.sh                # pick from default media roots
./linux/pick.sh /mnt/media/Tv  # pick from a specific root
```

1. Type a few letters to filter (e.g. `break` matches "Breaking Bad")
2. Arrow keys to highlight, Enter to select
3. Pick an action: translate, clean, mux, or dry-run variants
4. Script runs automatically

Edit the `DEFAULT_ROOTS` array at the top of `pick.sh` to set your media folder paths so you can run it without arguments.

### Translate a folder

Point the script at any folder containing video files. It scans recursively, so you can target a single movie folder, a TV series, or an entire library root.

**Windows (PowerShell):**

```powershell
# Translate a movie folder
.\translate_subs.ps1 "D:\Movies\Inception (2010)"

# Translate a TV series (all seasons)
.\translate_subs.ps1 "D:\TvSeries\Breaking Bad"

# Use a different LLM provider
.\translate_subs.ps1 "D:\Movies" -Profile openai

# Preview what would be translated
.\translate_subs.ps1 "D:\Media\Documentaries" -DryRun

# Limit to 5 files, retranslate existing, keep sidecars
.\translate_subs.ps1 "D:\Movies" -Limit 5 -Force -KeepSidecar

# UNC paths (network shares)
.\translate_subs.ps1 "\\nas\media\Movies"
```

**Linux (Bash):**

```bash
# Translate a movie folder
./linux/translate_subs.sh "/media/movies/Inception (2010)"

# Translate a TV series (all seasons)
./linux/translate_subs.sh "/media/tv/Breaking Bad"

# Use a different LLM provider
./linux/translate_subs.sh "/media/movies" --profile openai

# Preview what would be translated
./linux/translate_subs.sh "/media/documentaries" --dry-run

# Limit to 5 files, retranslate existing, keep sidecars
./linux/translate_subs.sh "/media/movies" --limit 5 --force --keep-sidecar

# NFS/SMB mounted shares work normally
./linux/translate_subs.sh "/mnt/nas/movies"
```

### Mux existing sidecar files into MKVs

```powershell
# Windows
.\mux_subs.ps1 "D:\Movies\Inception (2010)"
.\mux_subs.ps1 "D:\TvSeries\Show" -KeepSidecar -DryRun
```

```bash
# Linux
./linux/mux_subs.sh "/media/movies/Inception (2010)"
./linux/mux_subs.sh "/media/tv/Show" --keep-sidecar --dry-run
```

### Clean unwanted subtitle tracks

```powershell
# Windows
.\clean_subs.ps1 "D:\Movies"
.\clean_subs.ps1 "D:\TvSeries\Show" -DryRun
```

```bash
# Linux
./linux/clean_subs.sh "/media/movies"
./linux/clean_subs.sh "/media/tv/Show" --dry-run
```

### Sync video files between folders

```powershell
# Windows
.\sync-folder.ps1 "D:\TvSeries\Show" "\\nas\media\Tv\Show"
.\sync-folder.ps1 "D:\TvSeries\Show" "Z:\Tv\Show" -DryRun
```

```bash
# Linux
./linux/sync-folder.sh "/local/tv/Show" "/mnt/nas/tv/Show"
./linux/sync-folder.sh "/local/tv/Show" "/mnt/nas/tv/Show" --dry-run
```

## Scripts

| Script | Linux | Purpose |
|--------|-------|---------|
| `translate_subs.ps1` / `.py` | `linux/translate_subs.sh` | Main script — translate, mux, and clean in one pass |
| `mux_subs.ps1` / `.py` | `linux/mux_subs.sh` | Mux sidecar subtitles into MKV containers |
| `clean_subs.ps1` / `.py` | `linux/clean_subs.sh` | Remove unwanted subtitle tracks from MKVs |
| `sync-folder.ps1` | `linux/sync-folder.sh` | Sync video files between local and remote folders |
| `start-llama-server.ps1` | `linux/start-llama-server.sh` | Start local llama.cpp server for offline translation |
| `install.ps1` | `linux/install.sh` | Install dependencies and configure the project |
| `test_deepseek.py` | — | Standalone test for the translation API |

## How It Works

For each video file found:

1. **Check if already done** — skip if target language subs exist (embedded or sidecar)
2. **Find source subtitles** — try sidecar files first, then embedded tracks, in language priority order
3. **Detect untagged tracks** — if an embedded track has no language tag, extract a sample and ask the LLM to identify it, then tag the track in the MKV
4. **Translate** — send subtitle text to the LLM in batches, write result as sidecar file
5. **Mux** — embed the sidecar into the MKV as a target language track (MKV only)
6. **Clean** — remove unwanted subtitle tracks (anything not in the keep list, MKV only)

For non-MKV files (MP4, AVI, etc.), translation produces a sidecar `.srt` file that sits next to the video. Most players (Plex, Jellyfin, VLC, Kodi) pick up sidecar files automatically.

## Folder Structure

The scripts work with any folder structure. Just point them at a folder and they'll find all video files recursively:

```
Movies/
  Inception (2010)/
    Inception (2010).mkv

TvSeries/
  Breaking Bad/
    Season 01/
      Breaking.Bad.S01E01.mkv

Documentaries/
  Planet Earth II/
    Planet.Earth.II.E01.mkv

# Or point at a top-level library folder to process everything
Media/
  Movies/
  TvSeries/
  Documentaries/
```

## Troubleshooting

### ffmpeg not found

Install ffmpeg and ensure both `ffmpeg` and `ffprobe` are on your PATH:
- **Windows**: Download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or `winget install ffmpeg`
- **Debian/Ubuntu**: `sudo apt-get install ffmpeg`

### Python packages not found (Linux)

The Linux scripts use a virtual environment at `.venv/`. If you see import errors, re-run the install:

```bash
./linux/install.sh
```

Or manually install into the venv:

```bash
.venv/bin/pip install requests python-dotenv
```

### API timeout

For very large batches, the LLM API call may time out. The default timeout is 120 seconds per request. If you hit timeouts, try reducing `--batch-size` (default 500).

### Re-running after interruption

Safe to re-run at any time. Files with target language subs already present are skipped. Partially translated files (interrupted mid-write) will be retranslated.

## License

MIT
