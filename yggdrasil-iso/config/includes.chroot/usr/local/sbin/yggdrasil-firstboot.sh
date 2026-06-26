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

# --- Ensure the installed system can reach the Debian mirrors. The live installer often leaves
#     sources.list pointing only at the CD-ROM, which makes `apt install` fail with "no installation
#     candidate" for the NVIDIA driver below and for anything the user wants later (e.g. openssh). ---
if ! grep -rhqs 'debian\.org' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null; then
    cat > /etc/apt/sources.list <<EOF
deb http://deb.debian.org/debian trixie main contrib non-free non-free-firmware
deb http://deb.debian.org/debian trixie-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security trixie-security main contrib non-free non-free-firmware
EOF
    log "configured online apt sources (installer had left it CD-ROM only)"
fi
sed -i '/cdrom:/d' /etc/apt/sources.list 2>/dev/null || true
apt-get update -y >/dev/null 2>&1 || true

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

# --- ensure Ollama is up ---
systemctl start ollama.service 2>/dev/null || true
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done

# --- pull the model if missing, with retries. network-online.target can fire before connectivity
#     is actually usable, so wait for real internet and retry a few times. ---
if ! ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
    for attempt in 1 2 3 4 5; do
        for _ in $(seq 1 20); do curl -sf --max-time 5 https://registry.ollama.ai/ >/dev/null 2>&1 && break; sleep 3; done
        log "pulling ${MODEL} (attempt ${attempt})…"
        ollama pull "$MODEL" && break
        log "pull failed; retrying in 30s…"; sleep 30
    done
fi

# --- CRUCIAL: only mark first-boot done once the model is actually present, so a failed/interrupted
#     pull retries on the next boot instead of leaving the assistant brainless forever. ---
if ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
    touch "$STAMP"
    log "first-boot complete — ${MODEL} is ready"
else
    log "model ${MODEL} not present yet — first-boot will retry on the next boot"
fi
