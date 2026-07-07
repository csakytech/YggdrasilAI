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

# --- v0.9: the HUD launcher was never shipped ----------------------------------------------
# /etc/xdg/autostart/yggdrasil-hud.desktop Execs `yggdrasil-hud`, but the launcher itself
# was missing from every ISO — so the "Thinking…" status strip silently never started.
# Install it from the repo checkout; the autostart picks it up at next login.
HUD_SRC=/opt/yggdrasil/yggdrasil-iso/config/includes.chroot/usr/local/bin/yggdrasil-hud
if [ -f "$HUD_SRC" ] && [ ! -x /usr/local/bin/yggdrasil-hud ]; then
    install -m 755 "$HUD_SRC" /usr/local/bin/yggdrasil-hud || true
fi

exit 0
