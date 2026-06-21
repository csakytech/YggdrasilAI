#!/usr/bin/env bash
# Provision a fresh Debian 13 (trixie) install into a Yggdrasil OS dev box.
# Run on the RTX 3060 machine AFTER installing stock Debian. Safe to re-run.
#
#   sudo ./provision-debian.sh /path/to/YggdrasilOS
#
# Installs: NVIDIA driver, Python + audio + build deps, Ollama, the default local
# models, and the Yggdrasil app (into a venv). Then tells you to reboot.
set -euo pipefail

REPO="${1:-$HOME/YggdrasilOS}"
USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(eval echo "~$USER_NAME")"
VENV="$USER_HOME/yggdrasil-venv"

log() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0 $*"; exit 1; }

log "1/6  Configure APT sources (trixie + updates + security, all components)"
# Write a complete deb822 source. Robust even after a DVD install, which leaves only a
# disabled cdrom entry and no network mirror. Enables contrib + non-free + non-free-firmware
# so the NVIDIA driver and GPU firmware are installable.
cat > /etc/apt/sources.list.d/debian.sources <<'SRC'
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://deb.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SRC
# Neutralize any leftover cdrom entry from a DVD install.
[ -f /etc/apt/sources.list ] && sed -i 's/^deb cdrom:/#deb cdrom:/' /etc/apt/sources.list || true
apt-get update

log "2/6  NVIDIA driver + kernel headers (RTX 3060 / Ampere)"
# The driver is all Ollama needs for GPU. The full nvidia-cuda-toolkit (~2GB) is only
# needed later for voice (faster-whisper/cuDNN) and image gen — install it then:
#   sudo apt-get install -y nvidia-cuda-toolkit
apt-get install -y linux-headers-amd64 nvidia-driver nvidia-kernel-dkms firmware-misc-nonfree

log "3/6  Python, audio, build tools, utilities"
apt-get install -y python3 python3-venv python3-pip python3-dev build-essential \
    portaudio19-dev libasound2-dev alsa-utils pipewire pipewire-audio \
    curl ca-certificates jq pciutils git

log "4/6  Ollama"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh
systemctl enable --now ollama.service || true
# wait for the daemon
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done

log "4b/6 Store Ollama models on the largest filesystem (models are big; '/' is often small)"
# Pick the mount with the most free space; models can be tens of GB as the project grows.
BEST=$(df -BG --output=avail,target / /home /var 2>/dev/null | tail -n +2 | sort -rn | head -1 | awk '{print $2}')
[ -z "$BEST" ] && BEST=/var/lib
MODELS_DIR="${BEST%/}/ollama/models"
mkdir -p "$MODELS_DIR"
id ollama >/dev/null 2>&1 && chown -R ollama:ollama "${BEST%/}/ollama"
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/10-models.conf <<CONF
[Service]
Environment="OLLAMA_MODELS=$MODELS_DIR"
CONF
systemctl daemon-reload
systemctl restart ollama.service || true
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
echo "Ollama models -> $MODELS_DIR"

log "5/6  Pull default (12GB tier) + CPU-fallback models"
sudo -u "$USER_NAME" ollama pull qwen3:8b    || echo "WARN: qwen3:8b pull failed (check network)"
sudo -u "$USER_NAME" ollama pull llama3.2:3b || true

log "6/6  Install the Yggdrasil app into a venv (PEP 668-safe)"
if [ -d "$REPO/yggdrasil" ]; then
    sudo -u "$USER_NAME" python3 -m venv "$VENV"
    sudo -u "$USER_NAME" "$VENV/bin/pip" install -U pip
    sudo -u "$USER_NAME" "$VENV/bin/pip" install -e "$REPO/yggdrasil"
    APP_OK=1
else
    echo "WARN: repo not found at '$REPO'. Copy it over, then re-run with the path:"
    echo "      sudo $0 /home/$USER_NAME/YggdrasilOS"
    APP_OK=0
fi

log "Done. REBOOT to activate the NVIDIA driver."
cat <<EOF

  sudo reboot

After reboot:
  nvidia-smi                              # confirm the RTX 3060 is seen
$( [ "${APP_OK:-0}" = 1 ] && echo "  YGGDRASIL_MODEL=qwen3:8b $VENV/bin/yggdrasil   # talk to Jarvis with a real local model" )
EOF
