# ThorOS

> **Talk to your computer. It does the work.**

**ThorOS** is an AI-first, **local-first, voice-driven operating system** — a Debian 13 derivative you
install and then *speak to*. Instead of opening apps and clicking through menus, you say what you want,
and an on-device AI plans it and carries it out through a team of permissioned agents.

*Part of the **Yggdrasil** platform by **YggdrasilAI**. Releases are named after Norse gods — this one is **Thor**.*

[🌐 Website](https://www.yggdrasilai.org) · [⬇ Download ISO](https://github.com/csakytech/YggdrasilAI/releases/latest) · [🚀 Get Started](https://www.yggdrasilai.org/guide.html) · [💬 Discord](#discord) · [💚 Support](https://www.yggdrasilai.org/donate.html)

```
You → "Goal, in plain language" → on-device AI plans it → permissioned agents act → spoken result
```

## Why ThorOS

Most "AI assistants" are an app that chats and ships your life off to someone else's servers. ThorOS is
the opposite: the assistant **is** the way you use the computer, and by default **everything runs on
your own hardware** — the speech recognition, the language model, and the voice. Unplug the internet and
it still works. Connect a cloud model when you want more power — your choice, never required.

And it's voice-first for a reason: **so people who can't use a keyboard or mouse — severe arthritis,
injury, no hand use — can operate a computer and the web independently, in real time.** Accessibility
isn't a bonus feature here; it's the point.

## What it can do

🎙️ **Talk to it** — wake word → speech-to-text → plan → spoken reply, fully local. Prefer to type? A chat
window gives you the same assistant.

🛠️ **It does real work** — not just chat:
- **Files** — create / read / write / move / rename / search / open / delete, in a safe workspace. It
  understands approximate spoken names ("the reports folder", "the first one") and **always confirms
  before deleting**.
- **Apps** — launch *any* installed program by name, close them, write documents with the AI, browse and
  search the web.
- **Live answers** — "what's the price of Bitcoin?", weather, news → real-time, spoken back.
- **Reminders & briefings** — "remind me an hour before my meeting", "give me the Bitcoin report every
  weekday at 9am".
- **Terminal & system** — run commands (gated), check disk/processes; "list files" in a terminal just
  runs `ls`.
- **Turn a request into a command** *(prototype)* — "use the terminal to convert this to PDF" → it works
  out the command, previews it, and runs it safely sandboxed.

🧠 **It never dead-ends** — if no skill fits, a reasoning backbone still helps: it answers, points you to
the right command, or honestly offers the nearest path. It also **explains itself** ("why did you do
that?") and **remembers you** across sessions.

🔒 **You stay in control** — the AI never touches the OS directly. Dangerous actions need a spoken/typed
code or a yes/no confirm, with a hard block on catastrophic commands (it won't help wipe a disk).

🧩 **Community marketplace** — *install capabilities, not software.* Browse a catalog and **install AI
agents by voice**; they're usable instantly. Untrusted community agents run in a **bubblewrap sandbox**
(read-only system, their own folder only, no network); verified agents run trusted.

→ Full feature tour: **[yggdrasilai.org/features](https://www.yggdrasilai.org/features.html)**

## Try it

1. **[Download the ISO](https://github.com/csakytech/YggdrasilAI/releases/latest)** (v0.6).
2. Write it to a USB stick (Rufus / balenaEtcher / `dd`).
3. Boot it — try it live without installing, or install to disk.
4. New to this? The **[2-minute Getting Started guide](https://www.yggdrasilai.org/guide.html)** covers
   first boot and talking to Jarvis.

> A GPU helps (the assistant runs a local model), but ThorOS is hardware-agnostic and picks a model size
> from your hardware.

## For developers

ThorOS is built from swappable **agents**, each declaring its capabilities and running behind the
permission layer — so adding a skill doesn't mean touching the core.

- **Build an agent** — copy [`agent-template/`](agent-template/): a `manifest.toml` (identity,
  capabilities, permissions) + a Python class on the public SDK (`from yggdrasil.sdk import Agent,
  Capability`). See the template's README.
- **Architecture** — `Goal → Planner → Agents → Permission manager`. The planner is data-driven, so a
  newly installed agent becomes usable immediately.
- **The stack** — faster-whisper (STT), Ollama (local LLM), Piper (TTS), a GTK desktop, and bubblewrap
  (the agent sandbox).
- **The `.iso` is compiled, not copied** — a reproducible Debian `live-build` artifact. The app lives in
  `yggdrasil/`; the ISO recipe in `yggdrasil-iso/`.

## Repository layout

| Path | What |
|---|---|
| `yggdrasil/`     | The application — orchestrator, agents, voice loop (Python) |
| `yggdrasil-iso/` | The distribution recipe — `live-build` config that produces the `.iso` |
| `agent-template/`| Starter kit for building a marketplace agent |
| `website/`       | Source for [yggdrasilai.org](https://www.yggdrasilai.org) |
| `docs/`          | Architecture and decision records |

## <a name="discord"></a>Community

ThorOS is open and growing — contributors and curious people are welcome, whether you write code, build
an agent, test it on your hardware, or just want to follow along.

- 💬 **Discord** — chat, get help, and help shape ThorOS · **_invite link coming soon_**
- 🐙 **[GitHub](https://github.com/csakytech/YggdrasilAI)** — issues, discussions, and pull requests
- 💚 **[Support the project](https://www.yggdrasilai.org/donate.html)**

## Status

| Area | State |
|---|---|
| Voice assistant · files · apps · system · terminal | ✅ Shipping |
| Live research · reminders & briefings · memory | ✅ Shipping |
| Marketplace — install-by-voice + sandbox | ✅ Shipping |
| Spoken-name resolution + confirm-before-delete | ✅ Shipping |
| CLI-synthesis ("turn a request into a command") | 🧪 Prototype |
| Cloud-LLM option · GUI installer · autonomous project-builder | 🔜 Coming |

## License

ThorOS is licensed under **either of**

- Apache License, Version 2.0 ([`LICENSE-APACHE`](LICENSE-APACHE) · <https://www.apache.org/licenses/LICENSE-2.0>)
- MIT license ([`LICENSE-MIT`](LICENSE-MIT) · <https://opensource.org/licenses/MIT>)

at your option.

### Contribution

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this
work by you, as defined in the Apache-2.0 license, shall be dual licensed as above, without any
additional terms or conditions.

## Author note

Growing up watching films like *WarGames* with its WOPR computer, *HAL 9000*, and more, it has always
been a dream of mine to create an OS run by agents. Having studied neural networks, general AI, and
algorithmic computation from an early age, I've always been excited to bring AI to life. ThorOS isn't
built from the ground up — a stable Linux base handles the hardware layer, and the agents run throughout
the system, with control reaching all the way down the stack.

---

*ThorOS — powered by Yggdrasil AI. The Tree of Knowledge · The Root of Intelligence · The Future of Freedom.*
