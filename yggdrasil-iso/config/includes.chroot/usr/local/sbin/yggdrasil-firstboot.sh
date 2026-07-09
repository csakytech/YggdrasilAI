#!/bin/bash
# Runs once on the installed system (guarded by a stamp file). Detects the GPU vendor on the ACTUAL
# machine, installs the right proprietary driver if needed (only NVIDIA needs one for compute; the
# open in-kernel drivers handle display for everyone), chooses a model by VRAM, and pulls it (online
# edition). Keep the tiers in sync with core/llm.py MODEL_TIERS.
set -uo pipefail

# systemd services run with NO $HOME, but the Ollama CLI reads ~/.ollama at startup and PANICS
# ("$HOME is not defined") on every `ollama pull`/`ollama list` if it's unset — this silently broke
# the model download on first boot. Define it explicitly.
export HOME="${HOME:-/root}"

STAMP=/var/lib/yggdrasil/.firstboot-done
[ -f "$STAMP" ] && exit 0
mkdir -p "$(dirname "$STAMP")" /etc/yggdrasil /run/yggdrasil
log() { echo "yggdrasil-firstboot: $*"; }
# Publish a human-readable status the Welcome window reads (/run is tmpfs, recreated each boot).
setup_status() { echo "$*" > /run/yggdrasil/status 2>/dev/null; chmod 644 /run/yggdrasil/status 2>/dev/null || true; }
setup_status "Starting first-time setup…"

# --- Hands-free login: a voice-first OS must not park a keyboard-only login screen in front
#     of the very people it's built for. Enable GDM autologin for the primary user (UID 1000)
#     so the machine boots straight to the desktop with the assistant listening (also lets
#     scheduled briefings fire without a manual login). Turn it off anytime in
#     Settings > Users, or remove the two lines from /etc/gdm3/daemon.conf. ---
FIRST_USER=$(awk -F: '$3==1000{print $1; exit}' /etc/passwd)
if [ -n "$FIRST_USER" ] && ! grep -q '^AutomaticLoginEnable' /etc/gdm3/daemon.conf 2>/dev/null; then
    printf 'AutomaticLoginEnable=true\nAutomaticLogin=%s\n' "$FIRST_USER" >> /etc/gdm3/daemon.conf
    log "GDM autologin enabled for ${FIRST_USER}"
fi

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
    setup_status "Installing graphics driver (one-time)…"
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
    setup_status "Preparing to download your assistant…"
    for attempt in 1 2 3 4 5; do
        for _ in $(seq 1 20); do curl -sf --max-time 5 https://registry.ollama.ai/ >/dev/null 2>&1 && break; sleep 3; done
        log "pulling ${MODEL} (attempt ${attempt})…"
        # Stream ollama's progress (it uses \r) into the status file so the Welcome window shows a %.
        ollama pull "$MODEL" 2>&1 | tr '\r' '\n' | while IFS= read -r line; do
            case "$line" in
                *%*) setup_status "Downloading your assistant — $(printf '%s' "$line" | grep -oE '[0-9]+%' | tail -1)" ;;
            esac
        done
        ollama list 2>/dev/null | grep -q "${MODEL%%:*}" && break
        log "pull failed; retrying in 30s…"; sleep 30
    done
fi

# --- CRUCIAL: only mark first-boot done once the model is actually present, so a failed/interrupted
#     pull retries on the next boot instead of leaving the assistant brainless forever. ---
if ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
    touch "$STAMP"
    setup_status "Ready"
    systemctl disable --now yggdrasil-firstboot.timer 2>/dev/null || true
    log "first-boot complete — ${MODEL} is ready"
else
    setup_status "Still downloading — will keep trying…"
    log "model ${MODEL} not present yet — the firstboot timer will retry in a few minutes"
fi
