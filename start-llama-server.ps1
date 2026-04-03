<#
.SYNOPSIS
    Start llama-server for local subtitle translation.

.DESCRIPTION
    Launches llama-server with a GGUF model on the specified port.
    Uses OpenAI-compatible API at http://localhost:<Port>/v1/chat/completions

.EXAMPLE
    .\start-llama-server.ps1
    .\start-llama-server.ps1 -Model "models\bartowski\Qwen2.5-14B-Instruct-GGUF\Qwen2.5-14B-Instruct-Q4_K_M.gguf"
    .\start-llama-server.ps1 -Port 8080 -ContextSize 16384
#>
param(
    [string]$Model = "models\bartowski\Qwen_Qwen3-30B-A3B-GGUF\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf",
    [int]$Port = 1234,
    [int]$ContextSize = 8192,
    [int]$GpuLayers = 99
)

$ServerExe = Join-Path $PSScriptRoot "llama-server\llama-server.exe"
$ModelPath = Join-Path $PSScriptRoot $Model

if (-not (Test-Path $ServerExe)) {
    Write-Host "  [ERROR] llama-server.exe not found at: $ServerExe" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $ModelPath)) {
    Write-Host "  [ERROR] Model not found at: $ModelPath" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Model:     $Model" -ForegroundColor DarkGray
Write-Host "  Port:      $Port" -ForegroundColor DarkGray
Write-Host "  Context:   $ContextSize" -ForegroundColor DarkGray
Write-Host "  GPU layers: $GpuLayers" -ForegroundColor DarkGray
Write-Host "  KV cache:  K=Q8_0  V=Q4_0  (Flash Attention on)" -ForegroundColor DarkGray
Write-Host "  API:       http://localhost:$Port/v1/chat/completions" -ForegroundColor DarkGray
Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop the server." -ForegroundColor Yellow
Write-Host ""

& $ServerExe -m $ModelPath -ngl $GpuLayers -c $ContextSize -fa on -ctk q8_0 -ctv q4_0 --host 127.0.0.1 --port $Port
