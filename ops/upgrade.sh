#!/bin/bash
# ThorOS one-command upgrade — brings ANY existing install onto the self-updating track.
#
#     curl -fsSL https://www.yggdrasilai.org/upgrade.sh | sudo bash
#
# For machines installed from v0.1–v0.6 ISOs (before self-update existed): updates the app
# to the latest release AND installs the whole self-update system (helper, sudoers rule,
# login/7am check, HUD launcher, migrations) — so this is the LAST manual step they ever do.
# Safe to re-run anytime (idempotent); harmless on v0.7+ machines. User data is never touched.
set -e
APP=/opt/yggdrasil
SRC="$APP/yggdrasil-iso/config/includes.chroot"

[ "$(id -u)" = 0 ] || { echo "Please run with sudo:  curl -fsSL https://www.yggdrasilai.org/upgrade.sh | sudo bash"; exit 1; }
[ -d "$APP/.git" ] || { echo "No ThorOS app found at $APP — this machine needs a fresh install from yggdrasilai.org."; exit 1; }

echo "== ThorOS upgrade =="
git config --global --add safe.directory "$APP" 2>/dev/null || true
cd "$APP"

echo "-- fetching the latest release…"
git fetch --tags --quiet origin || { echo "Can't reach the update server. If your system clock is wrong, fix it first (this script will then also install automatic clock sync)."; exit 1; }
TAG=$(curl -fsSL https://www.yggdrasilai.org/updates/latest.json 2>/dev/null | tr -d ' "' | grep -o 'tag:v[0-9.]*' | cut -d: -f2 || true)
[ -n "$TAG" ] || TAG=origin/main
git reset --hard "$TAG" >/dev/null
echo "   app is now on $(git describe --tags --always)"

"$APP/venv/bin/pip" install -q -e "$APP" >/dev/null 2>&1 || true

echo "-- installing the self-update system…"
install -m 755 "$SRC/usr/local/sbin/yggdrasil-update"        /usr/local/sbin/yggdrasil-update
install -m 440 "$SRC/etc/sudoers.d/yggdrasil-update"         /etc/sudoers.d/yggdrasil-update
[ -f "$SRC/usr/local/bin/yggdrasil-update-check" ] && \
    install -m 755 "$SRC/usr/local/bin/yggdrasil-update-check" /usr/local/bin/yggdrasil-update-check
[ -f "$SRC/etc/xdg/autostart/yggdrasil-update-check.desktop" ] && \
    install -m 644 "$SRC/etc/xdg/autostart/yggdrasil-update-check.desktop" /etc/xdg/autostart/
[ -f "$SRC/etc/systemd/user/yggdrasil-update-check.service" ] && \
    install -m 644 "$SRC/etc/systemd/user/yggdrasil-update-check.service" /etc/systemd/user/ && \
    install -m 644 "$SRC/etc/systemd/user/yggdrasil-update-check.timer"   /etc/systemd/user/ && \
    systemctl --global enable yggdrasil-update-check.timer >/dev/null 2>&1 || true
[ -f "$SRC/etc/xdg/autostart/yggdrasil-hud.desktop" ] && \
    install -m 644 "$SRC/etc/xdg/autostart/yggdrasil-hud.desktop" /etc/xdg/autostart/

echo "-- applying system migrations (clock sync, status strip, …)…"
bash "$APP/ops/post-update.sh" || true

echo ""
echo "✅ Done. ThorOS is up to date and will keep ITSELF up to date from now on —"
echo "   it offers new versions at login, or just say: \"update yourself\"."
echo "   Log out and back in (or reboot) to load the new version. Your files are untouched."
