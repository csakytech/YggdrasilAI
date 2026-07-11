# ThorOS 1.1 — emergency fix for first-time setup

**If you installed ThorOS 1.0, please read the "Already on 1.0?" section below.**

ThorOS 1.1 is an urgent point release. Installing 1.0 on real hardware surfaced two
first-boot defects that could leave a fresh install permanently degraded. There are no
feature changes — 1.1 is 1.0 plus these fixes. If you haven't installed yet, simply use
the 1.1 ISO and everything below is handled for you.

## What was broken in the 1.0 ISO

1. **You could end up without admin rights.** If you set a *root password* during
   installation, the Debian installer silently skips adding your user account to the
   `sudo` group. Result: you can't administer your own machine — and because ThorOS
   self-updates require sudo membership, you couldn't even receive this fix
   automatically. First-boot setup now repairs the membership on every install.

2. **NVIDIA machines stayed on CPU forever.** First-boot setup installed the NVIDIA
   driver correctly, but the driver can only take over from the open-source one after a
   reboot — and nothing ever asked for one. The assistant ran CPU-only indefinitely, and
   the AI model was chosen while the GPU was still invisible, locking 12 GB cards to a
   smaller fallback model. Setup now performs one automatic restart right after the
   driver install (shown in the Welcome window, and guarded so it can never reboot-loop),
   then resumes and picks the model your GPU actually deserves.

## Already on 1.0? Fix your install in two minutes

Open a terminal (or the Files → Terminal app) and run:

```bash
su -                       # enter the ROOT password you chose during install
usermod -aG sudo YOUR_USERNAME
reboot
```

That restores your admin rights and — on NVIDIA machines — activates the already-installed
graphics driver. After the reboot, updates and everything else work normally. If you did
**not** set a root password during install, you only need the `reboot`.

*(Optional, NVIDIA 12 GB+ cards: your install may have selected the smaller `qwen3:8b`
model. It works great — it's the recommended model when using voice — but you can switch
models any time by voice: "use a different model".)*

## Checksums

`ThorOS-1.1-amd64.iso` — sha256 published on the release page.
