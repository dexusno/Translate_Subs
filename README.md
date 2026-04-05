# Translate Subs

Translate subtitles for your entire media library using the power of large language models. Point it at a folder and it handles everything — detecting existing subtitles in your files (embedded or external `.srt`/`.ass` files), translating them to your language, and cleaning up what you don't need.

Unlike traditional subtitle translation tools that do word-for-word replacement, Translate Subs uses LLMs that understand context, idioms, slang, and cultural references. The result is subtitles that read naturally — like they were written by a native speaker, not run through a machine translator.

Works with TV series, movies, documentaries — any media library, any folder structure. Runs on both Windows and Linux, supports cloud APIs and local models, and is designed to process large libraries efficiently over local drives or network shares.

## Highlights

- **Natural translations** — LLMs understand context, tone, and intent. Jokes land, slang makes sense, and dialogue flows naturally.
- **Fully configurable** — choose your target language, source languages, which languages to keep, and which LLM provider to use. Everything is in one config file.
- **Hands-off batch processing** — point it at a folder and walk away. It finds subtitles, translates them, and handles the rest. Re-running is safe — already translated files are skipped.
- **Fast** — streaming pipeline starts translating as soon as the first file is found. Translates up to 8 files in parallel. Directory caching eliminates slow lookups over network shares.
- **Flexible source detection** — finds subtitles in external files (`.srt`, `.ass`, including `.sdh`, `.hi`, `.forced` variants), embedded MKV tracks, and even untagged tracks (identified via LLM). If no preferred language is available, falls back to any language it can find — the LLM handles the rest.
- **Smart MKV handling** — translating, embedding, and cleaning happen in a single remux pass. One read, one write — half the I/O compared to doing them separately.
- **Multiple LLM providers** — DeepSeek, OpenAI, Groq, Mistral, OpenRouter, Ollama, LM Studio. Any OpenAI-compatible API works.
- **Cross-platform** — PowerShell wrappers for Windows, Bash wrappers for Linux. Same Python core on both.

## Requirements

- **Python 3.11+** with `requests` and `python-dotenv`
- **ffmpeg** and **ffprobe** on PATH
- An API key for at least one LLM provider (or a local model via Ollama / LM Studio)

## Quick Start

### Windows

```powershell
# One-liner install
irm https://raw.githubusercontent.com/dexusno/Translate_Subs/main/install.ps1 | iex

# Or manual
git clone https://github.com/dexusno/Translate_Subs.git
cd Translate_Subs
pip install requests python-dotenv
cp .env.example .env        # edit and add your API key

# Test
.\translate_subs.ps1 "D:\Media\Some Folder" -DryRun
```

### Linux (Debian / Ubuntu)

```bash
# One-liner install
curl -fsSL https://raw.githubusercontent.com/dexusno/Translate_Subs/main/linux/install.sh | bash

# Or manual
sudo apt-get install python3 python3-pip python3-venv ffmpeg mkvtoolnix git
git clone https://github.com/dexusno/Translate_Subs.git
cd Translate_Subs
python3 -m venv .venv
.venv/bin/pip install requests python-dotenv
cp .env.example .env        # edit and add your API key

# Test
./linux/translate_subs.sh "/media/tv/Some Show" --dry-run
```

---

## Configuration

All configuration lives in `llm_config.json`. No code changes needed.

### Target Language

The `target_language` block defines what you're translating **to** and which other languages you want to keep in your files:

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
|-------|-------------|
| `name` | The language name sent to the LLM in the translation prompt. |
| `codes` | All ISO codes that represent this language. If a file already has subtitles tagged with any of these codes, it's considered done and skipped. |
| `sidecar_code` | The code used in output filenames: `Movie.{code}.srt` |
| `mkv_tag` | The language tag applied when embedding translated subs into an MKV. |
| `keep_with` | Languages to keep alongside your target (see below). |

### Keeping other languages

`keep_with` controls which additional languages are allowed to remain in your MKV files. Your target language is always kept — you don't need to list it here.

This setting affects two things:

- **Embedded subtitle tracks** — tracks tagged with a `keep_with` language stay in the MKV. Tracks in any other language are removed during the clean step.
- **External subtitle files** — if an external `.srt` or `.ass` file exists for a `keep_with` language and that language isn't already embedded, it gets embedded into the MKV automatically. After processing, all recognized external subtitle files are cleaned up.

After processing, each MKV will contain only your target language and the languages listed in `keep_with`. Everything else is stripped out.

### Bitmap subtitle removal (PGS)

By default, bitmap-based subtitle tracks (PGS, DVD subs) are always removed regardless of language. These formats are incompatible with many players and workflows, and text-based subtitles (SRT) are preferred for translation.

If a file has target-language PGS subs but also has text-based subs in another language, the script will translate the text subs and remove the PGS tracks.

To keep PGS tracks instead, set `remove_bitmap_subs` to `false` in `llm_config.json`:

```json
"remove_bitmap_subs": false
```

### Examples

<details>
<summary><strong>French</strong> (keep English alongside)</summary>

```json
"target_language": {
  "name": "French",
  "codes": ["fr", "fra", "fre"],
  "sidecar_code": "fr",
  "mkv_tag": "fra",
  "keep_with": ["en", "eng"]
}
```
</details>

<details>
<summary><strong>German</strong> (keep English alongside)</summary>

```json
"target_language": {
  "name": "German",
  "codes": ["de", "deu", "ger"],
  "sidecar_code": "de",
  "mkv_tag": "deu",
  "keep_with": ["en", "eng"]
}
```
</details>

<details>
<summary><strong>Brazilian Portuguese</strong> (keep English and Spanish)</summary>

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

### Source Languages

The `source_languages` list controls which subtitle tracks can be used as a translation source. Languages are tried in priority order — the first match wins. You can add or reorder languages to suit your library.

### LLM Profiles

Choose your translation backend with `--profile`. Each profile is defined in `llm_config.json`:

| Profile | Provider | Model | Notes |
|---------|----------|-------|-------|
| `deepseek` | DeepSeek | deepseek-chat | **Recommended** — excellent quality, very low cost |
| `openai` | OpenAI | gpt-4o | High quality, higher cost |
| `groq` | Groq | llama-3.3-70b | Free tier available |
| `mistral` | Mistral | mistral-large | Good for European languages |
| `openrouter` | OpenRouter | deepseek/deepseek-chat | Access to many models |
| `ollama` | Ollama | qwen2.5:14b | Free, runs locally |
| `lmstudio` | LM Studio | (loaded model) | Free, runs locally |

**We recommend DeepSeek** as the default provider. It produces natural, context-aware translations at a fraction of the cost of other cloud APIs. In our experience, a typical 45-minute episode costs about 1 cent to translate. A full season runs about $0.10, and even translating 1,000 episodes stays under $10. See [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) for current rates. *We have no affiliation with DeepSeek and receive no benefit from recommending them — it's simply what works best for this use case.*

Adding a custom provider is easy — any OpenAI-compatible API works:

```json
"my-provider": {
  "api_url": "https://api.example.com/v1/chat/completions",
  "model": "model-name",
  "api_key_env": "MY_PROVIDER_API_KEY"
}
```

Then add `MY_PROVIDER_API_KEY=your-key` to `.env`. For local models that don't need a key, use `"api_key": "none"` instead.

### Per-Profile Tuning

Each profile can include performance settings. Cloud APIs handle large batches and high concurrency well. Local models benefit from smaller batches and longer timeouts:

```json
"deepseek": {
  "batch_size": 500,
  "parallel": 8
},
"local": {
  "batch_size": 25,
  "parallel": 1,
  "timeout": 600
}
```

| Setting | What it does | Cloud default | Local default |
|---------|-------------|---------------|---------------|
| `batch_size` | Subtitle groups per API call | 500 | 25 |
| `parallel` | Files translated concurrently | 8 | 1 |
| `timeout` | Seconds before an API call times out | 120 | 600 |

CLI flags (`--batch-size`, `--parallel`) override profile settings when specified.

---

## Usage

### Interactive folder picker (Linux)

Browse your media library and pick a folder without typing paths:

```bash
sudo apt install fzf          # one-time setup
./linux/pick.sh                # pick from your configured media roots
./linux/pick.sh /mnt/media/Tv  # or pick from a specific folder
```

Type a few letters to filter, arrow keys to select, then choose an action (translate, clean, mux, or dry-run). Media folder paths are stored in `media_roots.conf` (gitignored — survives `git pull`).

### Translate

Point the script at any folder. It scans recursively, so you can target a single movie, a TV series, or an entire library.

**Windows:**

```powershell
.\translate_subs.ps1 "D:\Movies\Inception (2010)"
.\translate_subs.ps1 "D:\TvSeries\Breaking Bad"
.\translate_subs.ps1 "D:\Media" -Profile openai -DryRun
.\translate_subs.ps1 "D:\Movies" -Limit 5 -Force -KeepSidecar
.\translate_subs.ps1 "\\nas\media\Movies"
```

**Linux:**

```bash
./linux/translate_subs.sh "/media/movies/Inception (2010)"
./linux/translate_subs.sh "/media/tv/Breaking Bad"
./linux/translate_subs.sh "/media" --profile openai --dry-run
./linux/translate_subs.sh "/media/movies" --limit 5 --force --keep-sidecar
./linux/translate_subs.sh "/mnt/nas/movies"
```

### Standalone tools

The main `translate_subs` script handles everything automatically — translating, embedding into MKV, and cleaning unwanted tracks in one pass. You don't need to run the tools below separately under normal use.

However, they're available as standalone scripts if you want to run just one step on its own:

**Embed external subtitles into MKVs** — useful if you have `.srt` files from another source that you want to embed:

```powershell
.\mux_subs.ps1 "D:\TvSeries\Show"                          # Windows
```
```bash
./linux/mux_subs.sh "/media/tv/Show"                        # Linux
```

**Clean unwanted tracks** — useful if you just want to strip unwanted languages without translating:

```powershell
.\clean_subs.ps1 "D:\Movies" -DryRun                        # Windows
```
```bash
./linux/clean_subs.sh "/media/movies" --dry-run              # Linux
```

---

## Scripts

| PowerShell | Linux | Purpose |
|------------|-------|---------|
| `translate_subs.ps1` / `.py` | `linux/translate_subs.sh` | Translate, embed, and clean in one pass |
| `mux_subs.ps1` / `.py` | `linux/mux_subs.sh` | Embed external subtitle files into MKV containers |
| `clean_subs.ps1` / `.py` | `linux/clean_subs.sh` | Remove unwanted subtitle tracks from MKVs |
| `start-llama-server.ps1` | `linux/start-llama-server.sh` | Start llama.cpp server for local translation |
| `install.ps1` | `linux/install.sh` | Install dependencies and configure the project |
| — | `linux/pick.sh` | Interactive folder picker (requires fzf) |

## How It Works

For each video file:

1. **Skip if done** — if the target language already exists (embedded or as an external file), the file is skipped.
2. **Find source subtitles** — checks external files first (`.srt`, `.ass`, including `.sdh`/`.hi`/`.forced` variants), then embedded tracks. Languages from the `source_languages` priority list are preferred.
3. **Detect untagged tracks** — if an embedded subtitle track has no language tag, a small sample is extracted and sent to the LLM for identification. The track is then tagged in the MKV.
4. **Fallback** — if no subtitle in the priority list is found, the script falls back to any available subtitle in any language — embedded tracks, external files, anything it can read. The LLM can translate from virtually any language, so even a Romanian or Polish subtitle is better than nothing. Fallback usage is logged as `[FALLBACK]` for easy review.
5. **Translate** — the subtitle text is sent to the LLM in batches. The response is reassembled into a properly formatted `.srt` file.
6. **Embed + clean** — in a single remux pass: the translated subs are embedded, any wanted-language external files are embedded, unwanted tracks are removed, and external subtitle files are cleaned up.

Translation starts as soon as the first file is ready — the scanning and translating phases overlap, so you don't wait for the entire library to be scanned before work begins.

## Supported File Formats

The script works with any common video format — MKV is not required.

| Format | Translation | Embedding | Track cleanup |
|--------|------------|-----------|---------------|
| **MKV** | Full support — translates from embedded or external subtitles | Translated subs + wanted external files are embedded directly into the MKV | Unwanted subtitle tracks are removed, external files cleaned up |
| **MP4, AVI, MOV, WebM, OGM** | Full support — translates from external `.srt`/`.ass` files | Not supported (these formats don't allow easy subtitle embedding without re-encoding) | Not applicable |

For non-MKV files, the translated subtitles are saved as a `.srt` file next to the video (e.g. `Movie.no.srt`). Existing external subtitle files are left untouched. Most players — Plex, Jellyfin, VLC, Kodi — pick up external `.srt` files automatically.

## Folder Structure

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

## Troubleshooting

**ffmpeg not found** — install ffmpeg and make sure both `ffmpeg` and `ffprobe` are on your PATH. Windows: `winget install ffmpeg`. Linux: `sudo apt-get install ffmpeg`.

**Python packages not found (Linux)** — the Linux scripts use a virtual environment at `.venv/`. Re-run `./linux/install.sh` or manually install: `.venv/bin/pip install requests python-dotenv`.

**API timeout** — if translations time out on large files, reduce `--batch-size` (default 500) or increase the `timeout` in your profile config.

**Safe to re-run** — already translated files are skipped. Partially translated files (interrupted mid-write) are retranslated. You can stop and resume at any time.

## Disclaimer

This software is provided as-is, without warranty of any kind. By using Translate Subs, you acknowledge the following:

- **File modification** — this tool modifies media files in place (remuxing MKV containers, deleting external subtitle files). While it uses atomic file operations and creates backups during remuxing, data loss is always possible. **Back up your media library before running on important files.**
- **Translation quality** — translations are generated by third-party LLM APIs or local models. Output quality depends on the model, the source material, and the language pair. Always spot-check translations before relying on them.
- **API costs** — cloud LLM providers charge per token. Processing a large library can incur significant costs depending on your provider and plan. Use `--dry-run` to preview what will be processed before committing.
- **Third-party services** — this tool sends subtitle text to external APIs (DeepSeek, OpenAI, etc.) for translation. Do not use it on content you are not authorized to share with these services.
- **Legal responsibility** — you are solely responsible for ensuring your use of this tool complies with applicable laws, including copyright and content licensing. The authors of this project are not responsible for how it is used.

## License

MIT
