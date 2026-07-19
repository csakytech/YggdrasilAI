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

# --- v1.4.1: power helper — reboot/shutdown by voice from any process context -----------------
PWR_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/sbin/yggdrasil-power
PWR_SUDO=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/etc/sudoers.d/yggdrasil-power
[ -f "$PWR_SRC" ] && install -m 755 "$PWR_SRC" /usr/local/sbin/yggdrasil-power || true
if [ -f "$PWR_SUDO" ] && [ ! -f /etc/sudoers.d/yggdrasil-power ]; then
    install -m 440 "$PWR_SUDO" /etc/sudoers.d/yggdrasil-power || true
fi

# --- v1.4: ThorAI Settings window — install its launcher + app entry on existing installs -----
SET_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/bin/yggdrasil-settings
SET_DESK=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/share/applications/yggdrasil-settings.desktop
[ -f "$SET_SRC" ] && install -m 755 "$SET_SRC" /usr/local/bin/yggdrasil-settings || true
[ -f "$SET_DESK" ] && install -m 644 "$SET_DESK" /usr/share/applications/yggdrasil-settings.desktop || true

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

exit 0
