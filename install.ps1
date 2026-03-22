<#
.SYNOPSIS
    Install and configure Translate Subs.

.DESCRIPTION
    Checks prerequisites (Python, ffmpeg), installs Python dependencies,
    and sets up the .env file for API keys.

.EXAMPLE
    .\install.ps1
    .\install.ps1 -PythonExe "C:\Python311\python.exe"
#>
param(
    [Parameter(Mandatory = $false)]
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host "    Translate Subs — Install" -ForegroundColor Cyan
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host ""

# ── Find Python ──────────────────────────────────────────────────────────────

if ($PythonExe -eq "") {
    # Try common locations
    $candidates = @(
        "python"
        "python3"
        "D:\anaconda3\python.exe"
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )

    foreach ($candidate in $candidates) {
        try {
            $ver = & $candidate --version 2>&1
            if ($ver -match "Python 3\.(\d+)") {
                $minor = [int]$Matches[1]
                if ($minor -ge 11) {
                    $PythonExe = $candidate
                    break
                }
            }
        } catch {
            continue
        }
    }
}

if ($PythonExe -eq "") {
    Write-Host "  [ERROR] Python 3.11+ not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install Python 3.11 or newer, then re-run this script." -ForegroundColor Yellow
    Write-Host "  You can also specify the path manually:" -ForegroundColor Yellow
    Write-Host '    .\install.ps1 -PythonExe "C:\path\to\python.exe"' -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$pyVersion = & $PythonExe --version 2>&1
Write-Host "  [OK] Python: $PythonExe ($pyVersion)" -ForegroundColor Green

# ── Check ffmpeg ─────────────────────────────────────────────────────────────

$ffmpegOk = $false
$ffprobeOk = $false

try {
    $null = & ffmpeg -version 2>&1
    $ffmpegOk = $true
} catch {}

try {
    $null = & ffprobe -version 2>&1
    $ffprobeOk = $true
} catch {}

if ($ffmpegOk -and $ffprobeOk) {
    Write-Host "  [OK] ffmpeg and ffprobe found on PATH" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] ffmpeg/ffprobe not found on PATH." -ForegroundColor Yellow
    Write-Host "            Install from https://www.gyan.dev/ffmpeg/builds/" -ForegroundColor Yellow
    Write-Host "            or run: winget install ffmpeg" -ForegroundColor Yellow
    Write-Host ""
}

# ── Install Python dependencies ──────────────────────────────────────────────

Write-Host ""
Write-Host "  Installing Python packages..." -ForegroundColor DarkGray

& $PythonExe -m pip install --quiet --upgrade requests python-dotenv

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] pip install failed." -ForegroundColor Red
    exit 1
}

Write-Host "  [OK] requests, python-dotenv installed" -ForegroundColor Green

# ── Set up .env ──────────────────────────────────────────────────────────────

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$envFile = Join-Path $scriptDir ".env"
$envExample = Join-Path $scriptDir ".env.example"

if (Test-Path -LiteralPath $envFile) {
    Write-Host "  [OK] .env already exists (not overwriting)" -ForegroundColor Green
} elseif (Test-Path -LiteralPath $envExample) {
    Copy-Item -LiteralPath $envExample -Destination $envFile
    Write-Host "  [OK] Created .env from .env.example" -ForegroundColor Green
    Write-Host ""
    Write-Host "  !! Edit .env and add your API key for your chosen provider." -ForegroundColor Yellow
    Write-Host "     See llm_config.json for available profiles." -ForegroundColor Yellow
} else {
    Write-Host "  [WARNING] .env.example not found — create .env manually." -ForegroundColor Yellow
}

# ── Update PowerShell wrappers with detected Python path ─────────────────────

$wrappers = @("translate_series.ps1", "mux_subs.ps1", "clean_subs.ps1")
$resolvedPython = (Get-Command $PythonExe -ErrorAction SilentlyContinue).Source
if (-not $resolvedPython) { $resolvedPython = $PythonExe }

Write-Host ""
Write-Host "  Python path for .ps1 wrappers: $resolvedPython" -ForegroundColor DarkGray

foreach ($wrapper in $wrappers) {
    $wrapperPath = Join-Path $scriptDir $wrapper
    if (Test-Path -LiteralPath $wrapperPath) {
        $content = Get-Content -LiteralPath $wrapperPath -Raw
        $updated = $content -replace '(?m)^\$PythonExe\s*=\s*"[^"]*"', "`$PythonExe    = `"$resolvedPython`""
        if ($updated -ne $content) {
            Set-Content -LiteralPath $wrapperPath -Value $updated -NoNewline
            Write-Host "  [OK] Updated $wrapper with Python path" -ForegroundColor Green
        } else {
            Write-Host "  [OK] $wrapper already configured" -ForegroundColor Green
        }
    }
}

# ── Verify ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  -----------------------------------" -ForegroundColor DarkGray

$configFile = Join-Path $scriptDir "llm_config.json"
if (Test-Path -LiteralPath $configFile) {
    $config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    $profiles = ($config.profiles | Get-Member -MemberType NoteProperty).Name -join ", "
    $default = $config.default_profile
    Write-Host "  Profiles:  $profiles" -ForegroundColor DarkGray
    Write-Host "  Default:   $default" -ForegroundColor DarkGray
}

Write-Host "  -----------------------------------" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Install complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. Edit .env and add your API key" -ForegroundColor White
Write-Host "    2. Test with:" -ForegroundColor White
Write-Host '       .\translate_series.ps1 "D:\TvSeries\Some Show" -DryRun' -ForegroundColor White
Write-Host ""
