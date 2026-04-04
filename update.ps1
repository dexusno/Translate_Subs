<#
.SYNOPSIS
    Update Translate Subs to the latest version.

.DESCRIPTION
    Pulls the latest changes from GitHub. If local files have been modified,
    stashes them first, applies the update, then restores your changes.
    Reinstalls Python dependencies in case new ones were added.

.EXAMPLE
    .\update.ps1
#>

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host "    Translate Subs - Update" -ForegroundColor Cyan
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $scriptDir

# Check we're in a git repo
if (-not (Test-Path ".git")) {
    Write-Host "  [ERROR] Not a git repository. Run this from the Translate_Subs folder." -ForegroundColor Red
    exit 1
}

# Check for local changes
$status = & git status --porcelain 2>&1
$hasChanges = ($status | Where-Object { $_ -match "^\s?M" }).Count -gt 0

if ($hasChanges) {
    Write-Host "  Local changes detected — stashing before update..." -ForegroundColor Yellow
    & git stash push -m "auto-stash before update" --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Failed to stash local changes." -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Changes stashed" -ForegroundColor Green
}

# Pull latest
Write-Host "  Pulling latest changes..." -ForegroundColor DarkGray
& git pull --ff-only 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARNING] Fast-forward pull failed. Trying rebase..." -ForegroundColor Yellow
    & git pull --rebase 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Pull failed. You may need to resolve conflicts manually." -ForegroundColor Red
        if ($hasChanges) {
            Write-Host "  Your stashed changes can be restored with: git stash pop" -ForegroundColor Yellow
        }
        exit 1
    }
}

Write-Host "  [OK] Updated to latest version" -ForegroundColor Green

# Restore stashed changes
if ($hasChanges) {
    Write-Host "  Restoring your local changes..." -ForegroundColor DarkGray
    & git stash pop --quiet 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARNING] Could not auto-restore changes. Run 'git stash pop' manually." -ForegroundColor Yellow
        Write-Host "            If there are conflicts, resolve them and run 'git stash drop'." -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] Local changes restored" -ForegroundColor Green
    }
}

# Reinstall Python dependencies (in case new ones were added)
$PythonExe = ""
foreach ($candidate in @("python", "python3")) {
    try {
        $null = & $candidate --version 2>&1
        $PythonExe = $candidate
        break
    } catch { continue }
}

if ($PythonExe -ne "") {
    Write-Host "  Updating Python packages..." -ForegroundColor DarkGray
    & $PythonExe -m pip install --quiet --upgrade requests python-dotenv 2>&1 | Out-Null
    Write-Host "  [OK] Python packages up to date" -ForegroundColor Green
}

# Show current version
$latestTag = & git describe --tags --abbrev=0 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  Version: $latestTag" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  Update complete." -ForegroundColor Green
Write-Host ""
