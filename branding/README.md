# Yggdrasil / ThorOS branding

Brand artwork shipped with the OS.

## Default wallpaper

Save the ThorOS wallpaper here as **`thoros-wallpaper.png`** (or `.jpg`).

When present, it is installed to `/usr/share/backgrounds/yggdrasil/thoros.png` in the ISO and
set as the **default GNOME desktop background** for every fresh install (via the dconf override
in `../yggdrasil-iso/config/includes.chroot/etc/dconf/`). Users can still change their own
wallpaper; this is just the out-of-the-box default, replacing Debian's stock backgrounds.

A high-resolution (≥1920×1080) version looks best on modern displays.
