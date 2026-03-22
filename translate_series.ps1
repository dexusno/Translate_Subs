<#
.SYNOPSIS
    Translate subtitles to Norwegian Bokmal for a TV/movie folder.

.DESCRIPTION
    PowerShell wrapper for translate_series.py. Scans a folder recursively for
    video files, finds subtitles in any supported source language (English,
    Danish, Swedish, etc.), and translates them to Norwegian using a
    configurable LLM backend.

.EXAMPLE
    .\translate_series.ps1 "D:\TvSeries\Beyond Paradise"
    .\translate_series.ps1 "\\nas\media\Tv\Ugly Betty" -DryRun
    .\translate_series.ps1 "D:\Movies\Some, Movie (2024)" -Profile deepseek -BatchSize 200
    .\translate_series.ps1 "D:\TvSeries\Show" -SkipClean -DryRun
    .\translate_series.ps1 "D:\TvSeries\Show" -Profile local -SkipDetect
#>
param(
    [Parameter(Mandatory = $false, Position = 0)]
    [string]$Folder,

    [Parameter(Mandatory = $false)]
    [int]$BatchSize = 500,

    [Parameter(Mandatory = $false)]
    [int]$Parallel = 3,

    [Parameter(Mandatory = $false)]
    [int]$Limit = 0,

    [Parameter(Mandatory = $false)]
    [switch]$Force,

    [Parameter(Mandatory = $false)]
    [string]$LogFile = "",

    [Parameter(Mandatory = $false)]
    [switch]$DryRun,

    [Parameter(Mandatory = $false)]
    [string]$Profile = "",

    [Parameter(Mandatory = $false)]
    [switch]$SkipClean,

    [Parameter(Mandatory = $false)]
    [switch]$SkipDetect,

    [Parameter(Mandatory = $false)]
    [switch]$KeepSidecar
)

# ── Configuration ─────────────────────────────────────────────────────────────

$PythonExe    = "D:\anaconda3\python.exe"
$PythonScript = Join-Path $PSScriptRoot "translate_series.py"

# ── Validation ────────────────────────────────────────────────────────────────

function Exit-WithError {
    param([string]$Message)
    Write-Host ""
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor Yellow
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Some Show"'
    Write-Host '    .\translate_series.ps1 "\\nas\media\Tv\Show Name" -DryRun'
    Write-Host '    .\translate_series.ps1 "D:\Movies" -BatchSize 200 -Parallel 2'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Long Show" -Limit 3'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Redo" -Force'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Show" -Profile deepseek -DryRun'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Show" -SkipClean'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Show" -SkipDetect'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Show" -KeepSidecar'
    Write-Host '    .\translate_series.ps1 "D:\TvSeries\Big Job" -LogFile "C:\logs\translate.log"'
    Write-Host ""
    exit 1
}

# Check folder argument
if ([string]::IsNullOrWhiteSpace($Folder)) {
    Exit-WithError "No folder specified."
}

# Normalize the path
try {
    if ($Folder -match '^\\\\') {
        # UNC path -- keep as-is, trim trailing slashes
        $ResolvedFolder = $Folder.TrimEnd('\', '/')
        if (-not (Test-Path -LiteralPath $ResolvedFolder -PathType Container)) {
            Exit-WithError "UNC path not accessible: $Folder"
        }
    } else {
        $ResolvedFolder = (Resolve-Path -LiteralPath $Folder -ErrorAction Stop).Path
    }
} catch {
    Exit-WithError "Folder not found: $Folder"
}

if (-not (Test-Path -LiteralPath $ResolvedFolder -PathType Container)) {
    Exit-WithError "Path is not a folder: $ResolvedFolder"
}

# Check Python executable
if (-not (Test-Path -LiteralPath $PythonExe)) {
    Exit-WithError "Python not found at: $PythonExe`n         Edit `$PythonExe at the top of this script."
}

# Check the Python script exists
if (-not (Test-Path -LiteralPath $PythonScript)) {
    Exit-WithError "translate_series.py not found at: $PythonScript"
}

# Warn if .env missing (non-dry-run only)
$EnvFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path -LiteralPath $EnvFile) -and -not $DryRun) {
    Write-Host "  [WARNING] .env not found at: $EnvFile" -ForegroundColor Yellow
    Write-Host "            API key must be set as an environment variable." -ForegroundColor Yellow
    Write-Host ""
}

# Validate numeric parameters
if ($BatchSize -lt 1) {
    Exit-WithError "BatchSize must be at least 1 (got $BatchSize)."
}
if ($Parallel -lt 1) {
    Exit-WithError "Parallel must be at least 1 (got $Parallel)."
}

# ── Run ───────────────────────────────────────────────────────────────────────

$pyArgs = @(
    $PythonScript
    "--batch-size", $BatchSize
    "--parallel", $Parallel
)

if ($Limit -gt 0) {
    $pyArgs += @("--limit", $Limit)
}

if ($Force) {
    $pyArgs += "--force"
}

if ($LogFile -ne "") {
    $pyArgs += @("--log-file", $LogFile)
}

if ($DryRun) {
    $pyArgs += "--dry-run"
}

if ($Profile -ne "") {
    $pyArgs += @("--profile", $Profile)
}

if ($SkipClean) {
    $pyArgs += "--skip-clean"
}

if ($SkipDetect) {
    $pyArgs += "--skip-detect"
}

if ($KeepSidecar) {
    $pyArgs += "--keep-sidecar"
}

$pyArgs += $ResolvedFolder

# Determine profile display name
$ProfileDisplay = if ($Profile -ne "") { $Profile } else { "(config default)" }

Write-Host ""
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Python:    $PythonExe" -ForegroundColor DarkGray
Write-Host "  Folder:    $ResolvedFolder" -ForegroundColor DarkGray
Write-Host "  Profile:   $ProfileDisplay" -ForegroundColor DarkGray
Write-Host "  Batch:     $BatchSize / Parallel: $Parallel" -ForegroundColor DarkGray
if ($Limit -gt 0) {
    Write-Host "  Limit:     $Limit files" -ForegroundColor DarkGray
}
if ($Force) {
    Write-Host "  Force:     ON (retranslating existing files)" -ForegroundColor Yellow
}
if ($SkipClean) {
    Write-Host "  Clean:     OFF (skipping post-processing)" -ForegroundColor Yellow
}
if ($SkipDetect) {
    Write-Host "  Detect:    OFF (skipping untagged track detection)" -ForegroundColor DarkGray
}
if ($KeepSidecar) {
    Write-Host "  Sidecar:   KEEP after mux" -ForegroundColor DarkGray
}
if ($LogFile -ne "") {
    Write-Host "  Log file:  $LogFile" -ForegroundColor DarkGray
}
if ($DryRun) {
    Write-Host "  Mode:      DRY-RUN" -ForegroundColor Yellow
}
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

& $PythonExe @pyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  [ERROR] Script exited with code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
