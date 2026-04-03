<#
.SYNOPSIS
    Sync video files from a local folder to a remote folder, preserving folder structure.

.DESCRIPTION
    Copies video files from source to destination, matching the subfolder structure
    (e.g. Season 01, Season 02). If a file already exists at the destination:
      - Same modification date  -> skip (already up to date)
      - Different date          -> delete destination file, then copy
    Deletes before copying instead of overwriting to avoid permission errors
    on network shares.

    Caches the destination file listing upfront in one pass to avoid slow
    per-file network round-trips.

.EXAMPLE
    .\sync-folder.ps1 "D:\TvSeries\Some Show" "\\nas\media\Tv\Some Show"
    .\sync-folder.ps1 "D:\TvSeries\Some Show" "Z:\Tv\Some Show" -DryRun
#>
param(
    [Parameter(Mandatory = $false, Position = 0)]
    [string]$Source,

    [Parameter(Mandatory = $false, Position = 1)]
    [string]$Destination,

    [Parameter(Mandatory = $false)]
    [switch]$DryRun
)

function Exit-WithError {
    param([string]$Message)
    Write-Host ""
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor Yellow
    Write-Host '    .\sync-folder.ps1 "D:\TvSeries\Some Show" "\\nas\media\Tv\Some Show"'
    Write-Host '    .\sync-folder.ps1 "D:\Local\Show" "Z:\Remote\Show" -DryRun'
    Write-Host ""
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Source))      { Exit-WithError "No source folder specified." }
if ([string]::IsNullOrWhiteSpace($Destination)) { Exit-WithError "No destination folder specified." }
if (-not (Test-Path -LiteralPath $Source))       { Exit-WithError "Source not found: $Source" }
if (-not (Test-Path -LiteralPath $Destination))  { Exit-WithError "Destination not found: $Destination" }

$mode = if ($DryRun) { "DRY-RUN" } else { "LIVE" }

Write-Host ""
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Sync Folder [$mode]" -ForegroundColor DarkGray
Write-Host "  Source:      $Source" -ForegroundColor DarkGray
Write-Host "  Destination: $Destination" -ForegroundColor DarkGray
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray

$videoExts = @(".mkv", ".mp4", ".avi", ".mov", ".webm", ".ogm")

# Cache ALL destination files in one network pass — avoids per-file round-trips
Write-Host "  Caching destination..." -ForegroundColor DarkGray -NoNewline
$destCache = @{}
$destDirs = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
Get-ChildItem -LiteralPath $Destination -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
    $destCache[$_.FullName.ToLower()] = $_
    $destDirs.Add((Split-Path $_.FullName -Parent).ToLower()) | Out-Null
}
# Also cache directories without files
Get-ChildItem -LiteralPath $Destination -Recurse -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $destDirs.Add($_.FullName.ToLower()) | Out-Null
}
Write-Host " $($destCache.Count) files cached." -ForegroundColor DarkGray
Write-Host ""

$sourceFiles = Get-ChildItem -LiteralPath $Source -Recurse -File | Where-Object { $videoExts -contains $_.Extension.ToLower() }
$copied   = 0
$skipped  = 0
$replaced = 0
$errors   = 0
$bytesCopied = 0

foreach ($file in $sourceFiles) {
    $relPath = $file.FullName.Substring($Source.TrimEnd('\').Length + 1)
    $destPath = Join-Path $Destination $relPath
    $destKey = $destPath.ToLower()

    # Ensure destination subfolder exists (check cache, not network)
    $destDir = Split-Path $destPath -Parent
    if (-not $destDirs.Contains($destDir.ToLower())) {
        if ($DryRun) {
            Write-Host "  [MKDIR]   $relPath" -ForegroundColor Cyan
        } else {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        $destDirs.Add($destDir.ToLower()) | Out-Null
    }

    if ($destCache.ContainsKey($destKey)) {
        $destFile = $destCache[$destKey]

        # Compare modification times (truncate to seconds — network shares lose sub-second precision)
        $srcDate  = $file.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
        $destDate = $destFile.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")

        if ($srcDate -eq $destDate) {
            $skipped++
            Write-Host "  [SKIP]    $relPath" -ForegroundColor DarkGray
            continue
        }

        # Dates differ — delete destination, then copy
        $sizeMB = [math]::Round($file.Length / 1MB, 1)
        if ($DryRun) {
            Write-Host "  [REPLACE] $relPath ($sizeMB MB) src=$srcDate dest=$destDate" -ForegroundColor Yellow
            $replaced++
            continue
        }

        try {
            cmd /c del /f /q "`"$destPath`""
            Copy-Item -LiteralPath $file.FullName -Destination $destPath -Force
            (Get-Item -LiteralPath $destPath).LastWriteTime = $file.LastWriteTime
            $replaced++
            $bytesCopied += $file.Length
            Write-Host "  [REPLACE] $relPath ($sizeMB MB)" -ForegroundColor Yellow
        } catch {
            $errors++
            Write-Host "  [ERROR]   $relPath : $_" -ForegroundColor Red
        }
    } else {
        # File doesn't exist at destination
        $sizeMB = [math]::Round($file.Length / 1MB, 1)
        if ($DryRun) {
            Write-Host "  [COPY]    $relPath ($sizeMB MB)" -ForegroundColor Green
            $copied++
            continue
        }

        try {
            Copy-Item -LiteralPath $file.FullName -Destination $destPath -Force
            (Get-Item -LiteralPath $destPath).LastWriteTime = $file.LastWriteTime
            $copied++
            $bytesCopied += $file.Length
            Write-Host "  [COPY]    $relPath ($sizeMB MB)" -ForegroundColor Green
        } catch {
            $errors++
            Write-Host "  [ERROR]   $relPath : $_" -ForegroundColor Red
        }
    }
}

$totalGB = [math]::Round($bytesCopied / 1GB, 2)

Write-Host ""
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Skipped:  $skipped (up to date)" -ForegroundColor DarkGray
Write-Host "  Copied:   $copied (new)" -ForegroundColor DarkGray
Write-Host "  Replaced: $replaced (date mismatch)" -ForegroundColor DarkGray
if (-not $DryRun) {
    Write-Host "  Transfer: $totalGB GB" -ForegroundColor DarkGray
}
if ($errors -gt 0) {
    Write-Host "  Errors:   $errors" -ForegroundColor Red
}
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""
