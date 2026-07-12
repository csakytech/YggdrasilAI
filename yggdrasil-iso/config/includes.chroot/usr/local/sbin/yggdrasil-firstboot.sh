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

# --- The Debian installer only puts the first user in the sudo group when NO root password was
#     set during install; set one and you get a user who can't administer their own machine —
#     and, worse, can't run the self-updater (its sudoers rule is %sudo-only). Repair it here. ---
if [ -n "$FIRST_USER" ] && ! id -nG "$FIRST_USER" 2>/dev/null | grep -qw sudo; then
    usermod -aG sudo "$FIRST_USER" \
        && log "added ${FIRST_USER} to the sudo group (installer skips this when a root password is set)"
fi

# --- Every ISO bakes the starter model (build fails otherwise — 0600 hook), so point the
#     assistant at it IMMEDIATELY: it can talk while drivers install, through the activation
#     reboot, and while the GPU-tier model downloads. Overwritten with the tier model later. ---
[ -f /etc/yggdrasil/model.env ] || echo "YGGDRASIL_MODEL=llama3.2:3b" > /etc/yggdrasil/model.env

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
#     This is where the DKMS compile happens — on this machine, for this kernel, only if NVIDIA.
#     The new module cannot evict nouveau in a running session, so the driver only becomes active
#     after a reboot — and we must reboot BEFORE the model tier is chosen, because under nouveau
#     nvidia-smi reports no VRAM and a 12GB card would get locked to the CPU-fallback model with
#     the stamp already written. So: install, then one guarded automatic reboot; setup resumes on
#     the next boot (the stamp isn't written yet) with the driver live and picks the right tier.
#     The once-only flag prevents a reboot loop when the module can never load (e.g. Secure Boot
#     rejecting the unsigned DKMS build) — in that case we log and continue on CPU. ---
nvidia_works() { nvidia-smi --query-gpu=memory.total --format=csv,noheader >/dev/null 2>&1; }
if [ "$VENDOR" = nvidia ] && ! nvidia_works; then
    REBOOT_FLAG=/var/lib/yggdrasil/.nvidia-reboot-tried
    if ! dpkg -s nvidia-driver >/dev/null 2>&1; then
        log "NVIDIA GPU found — installing the proprietary driver…"
        setup_status "Installing graphics driver (one-time)…"
        apt-get update -y >/dev/null 2>&1 && apt-get install -y nvidia-driver >/dev/null 2>&1 \
            || log "WARN: nvidia-driver install failed (no network?) — continuing on CPU"
    fi
    if dpkg -s nvidia-driver >/dev/null 2>&1 && ! nvidia_works; then
        if [ ! -f "$REBOOT_FLAG" ]; then
            touch "$REBOOT_FLAG"
            log "driver installed — restarting once to activate it (setup resumes automatically)"
            # Make the one-time restart UNMISSABLE (QA feedback: the user must be told, not
            # surprised): a critical desktop notification in the user's session plus a 30-second
            # countdown on the Welcome window's status line. Still automatic — a "please restart
            # later" prompt is how machines end up running on CPU forever.
            if [ -n "$FIRST_USER" ]; then
                FUID=$(id -u "$FIRST_USER" 2>/dev/null)
                sudo -u "$FIRST_USER" DISPLAY=:0 \
                    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${FUID:-1000}/bus" \
                    notify-send -u critical "ThorOS setup" \
                    "Your graphics driver is installed. ThorOS will restart in 30 seconds to activate it — nothing to do, setup continues automatically." \
                    2>/dev/null || true
            fi
            for s in 30 25 20 15 10 5; do
                setup_status "Graphics driver installed — restarting in ${s} seconds to activate it (automatic, setup continues after)…"
                sleep 5
            done
            setup_status "Restarting now…"
            systemctl reboot
            exit 0
        fi
        log "WARN: driver installed but still not active after the activation reboot (Secure Boot?) — continuing on CPU"
    fi
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
log "VRAM=${VRAM}MiB vendor=${VENDOR} -> tier model=${MODEL}"

# --- ensure Ollama is up ---
systemctl start ollama.service 2>/dev/null || true
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done

have_model() { ollama list 2>/dev/null | grep -q "${1%%:*}"; }

# --- AI OUT OF THE BOX: every ISO bakes the STARTER model (CPU tier), so the assistant answers
#     from the very first boot — no internet required, no multi-GB wait ("no AI in the system?"
#     — real v1.2 user feedback). The GPU-tier model is a background UPGRADE, not a gate. ---
STARTER="llama3.2:3b"
if have_model "$MODEL"; then
    echo "YGGDRASIL_MODEL=$MODEL" > /etc/yggdrasil/model.env
elif have_model "$STARTER"; then
    echo "YGGDRASIL_MODEL=$STARTER" > /etc/yggdrasil/model.env
    log "starter model active (${STARTER}); ${MODEL} will download in the background"
    setup_status "Your assistant is ready to talk — downloading a bigger model in the background…"
fi

# --- pull the tier model if missing, with retries. network-online.target can fire before
#     connectivity is actually usable, so wait for real internet and retry a few times. The
#     assistant keeps working on the starter model the whole time. ---
if ! have_model "$MODEL"; then
    for attempt in 1 2 3 4 5; do
        for _ in $(seq 1 20); do curl -sf --max-time 5 https://registry.ollama.ai/ >/dev/null 2>&1 && break; sleep 3; done
        log "pulling ${MODEL} (attempt ${attempt})…"
        # Stream ollama's progress (it uses \r) into the status file so the Welcome window shows a %.
        ollama pull "$MODEL" 2>&1 | tr '\r' '\n' | while IFS= read -r line; do
            case "$line" in
                *%*) setup_status "You can talk to me now — upgrading my brain in the background: $(printf '%s' "$line" | grep -oE '[0-9]+%' | tail -1)" ;;
            esac
        done
        have_model "$MODEL" && break
        log "pull failed; retrying in 30s…"; sleep 30
    done
fi

# --- stamp done only when the TIER model is in place (the timer keeps retrying the background
#     upgrade until then; the starter keeps the assistant alive throughout). Air-gapped machines
#     with no path to the tier model still work forever on the starter. ---
if have_model "$MODEL"; then
    echo "YGGDRASIL_MODEL=$MODEL" > /etc/yggdrasil/model.env
    touch "$STAMP"
    setup_status "Ready"
    systemctl disable --now yggdrasil-firstboot.timer 2>/dev/null || true
    log "first-boot complete — ${MODEL} is ready"
    # The upgraded model applies to NEW assistant sessions (next login/restart); the current
    # session keeps the starter. Warm the final model so the switch is instant.
    systemctl restart yggdrasil-preload.service 2>/dev/null || true
elif have_model "$STARTER"; then
    setup_status "Ready (bigger model still downloading in the background)"
    log "running on ${STARTER}; ${MODEL} not present yet — the firstboot timer will keep trying"
else
    setup_status "Still downloading — will keep trying…"
    log "no model present yet — the firstboot timer will retry in a few minutes"
fi
