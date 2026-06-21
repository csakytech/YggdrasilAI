#!/bin/bash
# Runs once on the installed system: detect GPU VRAM -> choose a model tier -> ensure the
# model is present (pull on the online edition) -> start the assistant. Guarded by a stamp
# file so it no-ops on every later boot. Keep tiers in sync with core/llm.py MODEL_TIERS.
set -euo pipefail

STAMP=/var/lib/yggdrasil/.firstboot-done
[ -f "$STAMP" ] && exit 0
mkdir -p "$(dirname "$STAMP")" /etc/yggdrasil

# --- detect VRAM (MiB); 0 if no NVIDIA GPU / driver not ready ---
VRAM=0
if command -v nvidia-smi >/dev/null 2>&1; then
    VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
           | head -n1 | tr -dc '0-9' || true)
fi
VRAM=${VRAM:-0}

# --- tier selection (mirror of MODEL_TIERS) ---
if   [ "$VRAM" -ge 24000 ]; then MODEL="qwen3:32b"
elif [ "$VRAM" -ge 16000 ]; then MODEL="qwen3:14b"
elif [ "$VRAM" -ge 12000 ]; then MODEL="qwen3:14b"   # RTX 3060 12GB target
elif [ "$VRAM" -ge 6000  ]; then MODEL="qwen3:8b"
else                             MODEL="llama3.2:3b" # CPU-only: degraded, not real-time
fi
echo "YGGDRASIL_MODEL=$MODEL" > /etc/yggdrasil/model.env
echo "first-boot: VRAM=${VRAM}MiB -> model=${MODEL}"

# --- ensure Ollama is up, then ensure the model is available ---
systemctl start ollama.service || true
for _ in $(seq 1 30); do
    curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
done

if ! ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
    # Offline edition already bundled a model; online edition pulls now.
    ollama pull "$MODEL" || echo "WARN: model pull failed (offline / no network?) — continuing"
fi

# --- start the assistant ---
systemctl enable --now yggdrasil.service 2>/dev/null || \
    echo "WARN: yggdrasil.service not installed yet (app .deb not in this build)"

touch "$STAMP"
