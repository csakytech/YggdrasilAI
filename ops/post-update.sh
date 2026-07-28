#!/bin/bash
# Release migrations — run as root by /usr/local/sbin/yggdrasil-update after the app code is
# swapped. MUST be idempotent (it runs on every update) and MUST never assume network beyond
# what the update itself needed. Keep each step tiny, guarded, and commented with the release
# that introduced it.

# --- v0.8: time sync (TLS depends on a correct clock) -------------------------------------
# ISOs v0.4–v0.7 shipped without an NTP client; a drifted clock breaks all HTTPS including
# the updater. New ISOs bake systemd-timesyncd; this backfills machines that update to v0.8+.
if ! dpkg -s systemd-timesyncd >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq systemd-timesyncd >/dev/null 2>&1 || true
fi
systemctl enable --now systemd-timesyncd >/dev/null 2>&1 || true

# --- v0.9 (reworked v1.2): keep the updater helper itself current ---------------------------
# ALWAYS refresh the helper from the release being installed (idempotent), so a fix to the
# helper propagates on the next update. The old grep-guard left a broken -x gate in place
# forever — which had silently skipped every migration in this file until v1.2-rc.
UPD_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/sbin/yggdrasil-update
if [ -f "$UPD_SRC" ]; then
    install -m 755 "$UPD_SRC" /usr/local/sbin/yggdrasil-update || true
fi

# --- v0.9: the HUD launcher was never shipped ----------------------------------------------
# /etc/xdg/autostart/yggdrasil-hud.desktop Execs `yggdrasil-hud`, but the launcher itself
# was missing from every ISO — so the "Thinking…" status strip silently never started.
# Install it from the repo checkout; the autostart picks it up at next login.
HUD_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/bin/yggdrasil-hud
if [ -f "$HUD_SRC" ] && [ ! -x /usr/local/bin/yggdrasil-hud ]; then
    install -m 755 "$HUD_SRC" /usr/local/bin/yggdrasil-hud || true
fi

# --- v1.2: voice software installs -----------------------------------------------------------
# The Software agent needs the validated root helper + its sudoers drop-in on machines that
# predate the v1.2 ISO. Refresh the helper on every update so fixes to it propagate too.
INST_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/sbin/yggdrasil-install
SUDO_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/sudoers.d/yggdrasil-install
if [ -f "$INST_SRC" ]; then
    install -m 755 "$INST_SRC" /usr/local/sbin/yggdrasil-install || true
fi
if [ -f "$SUDO_SRC" ] && [ ! -f /etc/sudoers.d/yggdrasil-install ]; then
    install -m 440 "$SUDO_SRC" /etc/sudoers.d/yggdrasil-install || true
fi

# --- v1.2: model preload at boot --------------------------------------------------------------
# The first spoken command after a reboot paid the full model cold-load (1m41s for qwen3:14b on
# the 3060 box) while Jarvis sat silent. Warm it into VRAM at boot instead. Refresh on every
# update; enable+start once.
PRE_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/bin/yggdrasil-preload
PRE_SVC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/systemd/system/yggdrasil-preload.service
if [ -f "$PRE_SRC" ] && [ -f "$PRE_SVC" ]; then
    install -m 755 "$PRE_SRC" /usr/local/bin/yggdrasil-preload || true
    install -m 644 "$PRE_SVC" /etc/systemd/system/yggdrasil-preload.service || true
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable yggdrasil-preload.service >/dev/null 2>&1 || true
    systemctl start --no-block yggdrasil-preload.service >/dev/null 2>&1 || true
fi

# --- v1.5: screen vision needs a capture tool; v1.5.1 adds SILENT capture + click-by-sight ----
# New ISOs bake these; existing installs updating via the feed have the Vision code but may lack
# the tools. scrot = silent capture (no shutter/flash — seamless); xdotool = pointer control for
# click-by-sight. gnome-screenshot stays as a flashy fallback. Guarded + non-fatal.
for pkg in scrot xdotool gnome-screenshot; do
    command -v "$pkg" >/dev/null 2>&1 && continue
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg" >/dev/null 2>&1 || true
done

# --- v1.4.1: power helper — reboot/shutdown by voice from any process context -----------------
PWR_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/sbin/yggdrasil-power
PWR_SUDO=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/sudoers.d/yggdrasil-power
[ -f "$PWR_SRC" ] && install -m 755 "$PWR_SRC" /usr/local/sbin/yggdrasil-power || true
if [ -f "$PWR_SUDO" ] && [ ! -f /etc/sudoers.d/yggdrasil-power ]; then
    install -m 440 "$PWR_SUDO" /etc/sudoers.d/yggdrasil-power || true
fi

# --- v1.4/1.5.2/1.5.4: GTK window launchers + their app entries on existing installs
# (1.5.4 adds `conversation` — the "what did you actually hear?" window)
for w in settings tasks conversation; do
    SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/bin/yggdrasil-$w
    DESK=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/share/applications/yggdrasil-$w.desktop
    [ -f "$SRC" ] && install -m 755 "$SRC" /usr/local/bin/yggdrasil-$w || true
    [ -f "$DESK" ] && install -m 644 "$DESK" /usr/share/applications/yggdrasil-$w.desktop || true
done

# --- v1.2: no screen lock on a voice appliance ------------------------------------------------
# Autologin (firstboot) + an idle lock screen demanding a password is a contradiction — users
# who can't type were locked out an hour in. Bake the no-lock system defaults onto existing
# installs too (users can re-enable in Settings > Privacy).
DCONF_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/dconf/db/local.d/00-yggdrasil-nolock
if [ -f "$DCONF_SRC" ] && [ -d /etc/dconf/db ]; then
    mkdir -p /etc/dconf/db/local.d /etc/dconf/profile
    grep -qs "system-db:local" /etc/dconf/profile/user 2>/dev/null \
        || printf 'user-db:user\nsystem-db:local\n' > /etc/dconf/profile/user
    install -m 644 "$DCONF_SRC" /etc/dconf/db/local.d/00-yggdrasil-nolock || true
    dconf update >/dev/null 2>&1 || true
fi

# --- v1.5.3: the sign-in choice ---------------------------------------------------------------
# The Welcome window now offers hands-free sign-in vs a password, applied through a validated
# root helper. Existing installs have the new UI after this update but would have no helper to
# call, so install it (+ its sudoers drop-in) here.
LGN_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/sbin/yggdrasil-login-mode
LGN_SUDO=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/sudoers.d/yggdrasil-login
[ -f "$LGN_SRC" ] && install -m 755 "$LGN_SRC" /usr/local/sbin/yggdrasil-login-mode || true
if [ -f "$LGN_SUDO" ] && [ ! -f /etc/sudoers.d/yggdrasil-login ]; then
    install -m 440 "$LGN_SUDO" /etc/sudoers.d/yggdrasil-login || true
fi

# --- v1.5.3: hands-free login on the FIRST boot ------------------------------------------------
# New ISOs enable autologin before GDM starts (it used to be written by firstboot, far too late,
# so it only took effect on the second boot). Install the unit here for consistency, but STAMP it
# immediately: this machine has already been through first boot and its login state is settled —
# re-imposing autologin on someone who deliberately chose a password would be a nasty surprise.
ALG_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/sbin/yggdrasil-autologin
ALG_SVC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/systemd/system/yggdrasil-autologin.service
if [ -f "$ALG_SRC" ] && [ -f "$ALG_SVC" ]; then
    install -m 755 "$ALG_SRC" /usr/local/sbin/yggdrasil-autologin || true
    install -m 644 "$ALG_SVC" /etc/systemd/system/yggdrasil-autologin.service || true
    mkdir -p /var/lib/yggdrasil
    touch /var/lib/yggdrasil/.autologin-configured || true
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable yggdrasil-autologin.service >/dev/null 2>&1 || true
fi

exit 0
