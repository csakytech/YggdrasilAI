# yggdrasil-iso (the distribution recipe)

This is **how the `.iso` is compiled**, not a copy of a machine. It is a Debian
[`live-build`](https://wiki.debian.org/DebianLive/live-build) recipe: a set of config files
that `lb build` turns into a bootable, installable Yggdrasil OS image — reproducibly, from
git. Build it in a Debian 13 ("trixie") environment (a VM is fine).

## What it produces

A hybrid ISO (`live-image-amd64.hybrid.iso`) that boots as a live system and installs to disk,
including: GNOME desktop, NVIDIA driver + CUDA (from `non-free`), Python + the `yggdrasil`
app, Ollama, and a **first-boot setup** that detects the GPU, picks a model tier, pulls the
model (online edition), and starts the assistant.

## One recipe, two editions

```bash
# Lean / online (~3-4 GB): LLM pulled on first boot
sudo lb clean --purge && YGG_EDITION=online lb config && sudo lb build   # YGG_EDITION matters at CONFIG time

# Offline / bundled (~9-10 GB): default LLM baked into the image (air-gapped installs)
sudo lb clean --purge && YGG_EDITION=offline lb config && sudo lb build
```

Drop the built app package into `config/packages.chroot/yggdrasil_*.deb` before building
(packaging the app as a `.deb` is the next milestone; until then the hooks tolerate its
absence).

## Build host setup (Debian 13)

```bash
sudo apt install live-build debootstrap xorriso squashfs-tools qemu-system-x86 ovmf
```

Needs root (chroot/mounts) and ~20 GB free (online) / ~40 GB (offline).

## Test in a VM before bare metal

```bash
qemu-img create -f qcow2 test.qcow2 40G
qemu-system-x86_64 -enable-kvm -m 8192 -smp 4 \
  -bios /usr/share/ovmf/OVMF.fd \
  -cdrom live-image-amd64.hybrid.iso \
  -drive file=test.qcow2,if=virtio,format=qcow2 -boot d -vga virtio
```

A VM validates the desktop, installer, first-boot stamp logic, and the Ollama daemon. The
NVIDIA/CUDA path and the 8B model only exercise fully on the real RTX 3060 box.

## Layout

```
auto/config                          fixed `lb config` flags (reproducible builds)
config/package-lists/*.list.chroot   packages installed into the system
config/hooks/normal/*.hook.chroot    build-time customization (root scripts in the chroot)
config/includes.chroot/              files overlaid verbatim into the target rootfs
config/packages.chroot/              local .deb files (drop yggdrasil_*.deb here)
```

See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §6 for how this fits the whole.
