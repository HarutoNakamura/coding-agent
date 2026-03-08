#!/usr/bin/env bash
# ============================================================
# LFM2-350M-PII-Extract-JP llama-server 起動スクリプト
# ポート: 8766 (コーディングエージェント: 8765 と衝突しない)
# ============================================================

set -e

MODEL_DIR="/Users/admin/Desktop/compe/LFM2-350M-PII-Extract-JP-GGUF"

# デフォルトは Q4_K_M（速度・精度バランス）。引数で変更可能。
# 使い方: ./start_pii_server.sh Q8_0
QUANT="${1:-Q4_K_M}"
MODEL_FILE="${MODEL_DIR}/LFM2-350M-PII-Extract-JP-${QUANT}.gguf"

PORT=8766
HOST="127.0.0.1"

# ---- 事前チェック ----
if ! command -v llama-server &>/dev/null; then
    echo "[ERROR] llama-server not found. Install with: brew install llama.cpp"
    exit 1
fi

if [ ! -f "$MODEL_FILE" ]; then
    echo "[ERROR] Model not found: $MODEL_FILE"
    echo "Available quantizations: Q4_0, Q4_K_M, Q5_K_M, Q6_K, Q8_0, F16"
    exit 1
fi

# ---- 既に起動しているか確認 ----
if curl -s "http://${HOST}:${PORT}/health" | grep -q "ok" 2>/dev/null; then
    echo "[INFO] llama-server already running on port ${PORT}"
    exit 0
fi

echo "=========================================="
echo " LFM2 PII Extractor Server"
echo " Model : $(basename "$MODEL_FILE")"
echo " Listen: http://${HOST}:${PORT}"
echo "=========================================="

exec llama-server \
    --model "$MODEL_FILE" \
    --host "$HOST" \
    --port "$PORT" \
    --temp 0.0 \
    --jinja \
    --ctx-size 4096 \
    -ngl 99 \
    --log-disable
