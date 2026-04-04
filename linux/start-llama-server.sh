#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# start-llama-server.sh — Start llama-server for local subtitle translation.
#
# Launches llama-server with a GGUF model on the specified port.
# Uses OpenAI-compatible API at http://localhost:<port>/v1/chat/completions
#
# Usage:
#   ./start-llama-server.sh
#   ./start-llama-server.sh --model models/bartowski/Qwen2.5-14B-Instruct-GGUF/Qwen2.5-14B-Instruct-Q4_K_M.gguf
#   ./start-llama-server.sh --port 8080 --context 16384
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults
MODEL="models/bartowski/Qwen_Qwen3-30B-A3B-GGUF/Qwen_Qwen3-30B-A3B-Q4_K_M.gguf"
PORT=1234
CONTEXT_SIZE=8192
GPU_LAYERS=99

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)    MODEL="$2"; shift 2 ;;
        --port)     PORT="$2"; shift 2 ;;
        --context)  CONTEXT_SIZE="$2"; shift 2 ;;
        --gpu)      GPU_LAYERS="$2"; shift 2 ;;
        *)          echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Find llama-server
SERVER_EXE=""
if command -v llama-server &>/dev/null; then
    SERVER_EXE="llama-server"
elif [[ -x "$PROJECT_DIR/llama-server/llama-server" ]]; then
    SERVER_EXE="$PROJECT_DIR/llama-server/llama-server"
elif [[ -x "/usr/local/bin/llama-server" ]]; then
    SERVER_EXE="/usr/local/bin/llama-server"
else
    echo "  [ERROR] llama-server not found."
    echo "          Install llama.cpp or place llama-server binary in the project."
    exit 1
fi

MODEL_PATH="$PROJECT_DIR/$MODEL"
if [[ ! -f "$MODEL_PATH" ]]; then
    # Try as absolute path
    if [[ -f "$MODEL" ]]; then
        MODEL_PATH="$MODEL"
    else
        echo "  [ERROR] Model not found at: $MODEL_PATH"
        exit 1
    fi
fi

echo ""
echo "  ---------------------------------------------------"
echo "  Model:      $MODEL"
echo "  Port:       $PORT"
echo "  Context:    $CONTEXT_SIZE"
echo "  GPU layers: $GPU_LAYERS"
echo "  KV cache:   K=Q8_0  V=Q4_0  (Flash Attention on)"
echo "  API:        http://localhost:$PORT/v1/chat/completions"
echo "  ---------------------------------------------------"
echo ""
echo "  Press Ctrl+C to stop the server."
echo ""

exec "$SERVER_EXE" \
    -m "$MODEL_PATH" \
    -ngl "$GPU_LAYERS" \
    -c "$CONTEXT_SIZE" \
    -fa on \
    -ctk q8_0 \
    -ctv q4_0 \
    --host 127.0.0.1 \
    --port "$PORT"
