# Setting up the Yggdrasil dev box (RTX 3060 machine)

Goal: turn the spare 3060 machine into a native Debian 13 box running Yggdrasil with a real
local model. This box becomes both our **dev environment** and, later, the **build host** for
the custom `yggdrasil.iso`.

> You do not need a custom ISO to run Yggdrasil on your own machine — you install stock Debian
> and run the app on top. The custom `.iso` is a later artifact for distributing to others.

## 0. Before you install (do this first)

- If upgrading to a **1TB SSD** and/or **32GB RAM**, install the hardware **now**, so Debian
  lands on the SSD and you don't install twice.
- You have the installer image already: `debian-13.5.0-amd64-DVD-1.iso`.
- You'll need a spare **USB stick (8GB+)** — it will be erased.

## 1. Make a bootable USB (on Windows)

Use [Rufus](https://rufus.ie):
1. Insert the USB stick.
2. Rufus → **Device** = your USB → **Boot selection** = `debian-13.5.0-amd64-DVD-1.iso`.
3. Leave defaults (GPT / UEFI). Click **Start**. If prompted ISO vs DD mode, choose
   **Write in ISO Image mode**.

## 2. Install Debian 13 on the 3060 box

Boot the box from the USB (tap the boot-menu key — usually F12/F11/F8 — and pick the USB; the
i7-3770 era may need Secure Boot disabled in BIOS).

Choose **Graphical Install** and accept defaults except:
- **Hostname:** `yggdrasil` (anything is fine).
- **Network:** connect it (Ethernet is easiest) so firmware/updates can be fetched.
- **Partitioning:** *Guided — use entire disk* (this is a dedicated box — pick the **SSD**).
- **Software selection** (spacebar to toggle): keep **GNOME**, and **add**:
  - ☑ **SSH server** ← so you can manage it headless from your Windows machine
  - ☑ standard system utilities
- Set a user/password you'll remember.

Reboot when it finishes; remove the USB.

Find the box's IP for later (on the box: `ip addr` — look for `192.168...` / `10.0.0...`).

## 3. Get this repo onto the box

Easiest from your Windows machine (the box has SSH now). In PowerShell:

```powershell
scp -r "E:\Downloads\Development\YggdrasilOS" <user>@<box-ip>:~/
```

(Or copy the folder via the same USB stick. Later we'll push to GitHub for clean syncing.)

## 4. Provision (one command)

SSH in from Windows: `ssh <user>@<box-ip>`, then:

```bash
cd ~/YggdrasilOS
chmod +x scripts/provision-debian.sh
sudo ./scripts/provision-debian.sh ~/YggdrasilOS
```

This installs the NVIDIA driver, Python/audio/build deps, Ollama, the default models
(`qwen3:8b` + `llama3.2:3b` fallback), and the app into a venv. It finishes by telling you to
reboot.

## 5. Reboot and talk to Jarvis (with a real model)

```bash
sudo reboot
# after it comes back, SSH in again:
nvidia-smi                                          # should list the RTX 3060
YGGDRASIL_MODEL=qwen3:8b ~/yggdrasil-venv/bin/yggdrasil
```

Now the **LLM planner** is live (not the heuristic one): the local Qwen3-8B model turns your
typed goals into File Agent actions, schema-constrained for reliability.

## What comes after this

1. Validate + tune the LLM planner on the real GPU.
2. Wire the voice loop (`voice/loop.py`): openWakeWord + faster-whisper + Piper.
3. Package the app as a `.deb`.
4. On this same box, run `live-build` (`../yggdrasil-iso/`) to produce `yggdrasil.iso`, then
   boot-test it in a VM. **Now** the custom ISO makes sense — there's a real, packaged system
   to put in it.
