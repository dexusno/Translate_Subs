<#
.SYNOPSIS
    Remove unwanted subtitle tracks from MKV files.

.DESCRIPTION
    PowerShell wrapper for clean_subs.py. Scans a folder recursively for MKV
    files and removes subtitle tracks that are not Norwegian, English, Danish,
    or Swedish.

.EXAMPLE
    .\clean_subs.ps1 "D:\TvSeries\Some Show"
    .\clean_subs.ps1 "\\nas\media\Tv\Show Name" -DryRun
    .\clean_subs.ps1 "D:\TvSeries\Big Lib" -Limit 10 -SkipDetect
#>
param(
    [Parameter(Mandatory = $false, Position = 0)]
    [string]$Folder,

    [Parameter(Mandatory = $false)]
    [int]$Limit = 0,

    [Parameter(Mandatory = $false)]
    [string]$LogFile = "",

    [Parameter(Mandatory = $false)]
    [switch]$DryRun,

    [Parameter(Mandatory = $false)]
    [switch]$SkipDetect
)

# ── Configuration ─────────────────────────────────────────────────────────────

$PythonExe    = "D:\anaconda3\python.exe"
$PythonScript = Join-Path $PSScriptRoot "clean_subs.py"

# ── Validation ────────────────────────────────────────────────────────────────

function Exit-WithError {
    param([string]$Message)
    Write-Host ""
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor Yellow
    Write-Host '    .\clean_subs.ps1 "D:\TvSeries\Some Show"'
    Write-Host '    .\clean_subs.ps1 "\\nas\media\Tv\Show Name" -DryRun'
    Write-Host '    .\clean_subs.ps1 "D:\TvSeries\Big Lib" -Limit 10'
    Write-Host '    .\clean_subs.ps1 "D:\TvSeries\Show" -SkipDetect'
    Write-Host '    .\clean_subs.ps1 "D:\TvSeries\Show" -LogFile "C:\logs\clean.log"'
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
    Exit-WithError "clean_subs.py not found at: $PythonScript"
}

# Warn if .env missing (non-dry-run only)
$EnvFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path -LiteralPath $EnvFile) -and -not $DryRun -and -not $SkipDetect) {
    Write-Host "  [WARNING] .env not found at: $EnvFile" -ForegroundColor Yellow
    Write-Host "            DEEPSEEK_API_KEY must be set as an environment variable." -ForegroundColor Yellow
    Write-Host ""
}

# ── Run ───────────────────────────────────────────────────────────────────────

$pyArgs = @($PythonScript)

if ($Limit -gt 0) {
    $pyArgs += @("--limit", $Limit)
}

if ($LogFile -ne "") {
    $pyArgs += @("--log-file", $LogFile)
}

if ($DryRun) {
    $pyArgs += "--dry-run"
}

if ($SkipDetect) {
    $pyArgs += "--skip-detect"
}

$pyArgs += $ResolvedFolder

Write-Host ""
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Python:    $PythonExe" -ForegroundColor DarkGray
Write-Host "  Folder:    $ResolvedFolder" -ForegroundColor DarkGray
if ($Limit -gt 0) {
    Write-Host "  Limit:     $Limit files" -ForegroundColor DarkGray
}
if ($LogFile -ne "") {
    Write-Host "  Log file:  $LogFile" -ForegroundColor DarkGray
}
if ($SkipDetect) {
    Write-Host "  Detect:    OFF (keeping undefined-language tracks)" -ForegroundColor DarkGray
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
