#!/bin/bash
# Runs once on the installed system (guarded by a stamp file). Detects the GPU vendor on the ACTUAL
# machine, installs the right proprietary driver if needed (only NVIDIA needs one for compute; the
# open in-kernel drivers handle display for everyone), chooses a model by VRAM, and pulls it (online
# edition). Keep the tiers in sync with core/llm.py MODEL_TIERS.
set -uo pipefail

STAMP=/var/lib/yggdrasil/.firstboot-done
[ -f "$STAMP" ] && exit 0
mkdir -p "$(dirname "$STAMP")" /etc/yggdrasil
log() { echo "yggdrasil-firstboot: $*"; }

# --- detect GPU vendor (works with just the open drivers — no proprietary driver needed) ---
GPU=$(lspci -nn 2>/dev/null | grep -iE 'vga|3d controller|display controller' | head -1)
case "$GPU" in
    *NVIDIA*|*"[10de:"*)                    VENDOR=nvidia ;;
    *"Advanced Micro Devices"*|*ATI*|*"[1002:"*) VENDOR=amd ;;
    *Intel*|*"[8086:"*)                     VENDOR=intel ;;
    *)                                      VENDOR=none ;;
esac
log "GPU vendor: ${VENDOR} (${GPU:-none detected})"

# --- NVIDIA only: install the proprietary driver for CUDA/Ollama (nouveau already drives display).
#     This is where the DKMS compile happens — on this machine, for this kernel, only if NVIDIA. ---
if [ "$VENDOR" = nvidia ] && ! command -v nvidia-smi >/dev/null 2>&1; then
    log "NVIDIA GPU found — installing the proprietary driver (GPU active after the next reboot)…"
    apt-get update -y >/dev/null 2>&1 && apt-get install -y nvidia-driver >/dev/null 2>&1 \
        || log "WARN: nvidia-driver install failed (no network?) — continuing on CPU"
fi

# --- choose a model by VRAM (mirror of MODEL_TIERS) ---
VRAM=0
if command -v nvidia-smi >/dev/null 2>&1; then
    VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc '0-9')
fi
VRAM=${VRAM:-0}
if   [ "$VRAM" -ge 24000 ]; then MODEL="qwen3:32b"
elif [ "$VRAM" -ge 16000 ]; then MODEL="qwen3:14b"
elif [ "$VRAM" -ge 12000 ]; then MODEL="qwen3:14b"
elif [ "$VRAM" -ge 6000  ]; then MODEL="qwen3:8b"
elif [ "$VENDOR" = nvidia ]; then MODEL="qwen3:8b"    # NVIDIA present, driver not loaded yet -> assume 8b
else                              MODEL="llama3.2:3b" # AMD/Intel/none -> CPU (degraded, warn user)
fi
echo "YGGDRASIL_MODEL=$MODEL" > /etc/yggdrasil/model.env
log "VRAM=${VRAM}MiB vendor=${VENDOR} -> model=${MODEL}"

# --- ensure Ollama is up, then pull the model (online edition; offline already bundled one) ---
systemctl start ollama.service 2>/dev/null || true
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
ollama list 2>/dev/null | grep -q "${MODEL%%:*}" || ollama pull "$MODEL" \
    || log "WARN: model pull failed (offline / no network?) — continuing"

touch "$STAMP"
log "first-boot setup complete"
