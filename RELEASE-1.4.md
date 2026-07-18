# ThorOS 1.4 — talk WITH it, not at it

ThorOS 1.4 makes the assistant a conversation partner: interrupt him mid-sentence, follow up
without the wake word, ask him to repeat himself, chat in a window like the AI chatbots you
know — and it all ships with a brain in the box. (This release folds in the unpublished 1.3
work, so it's two releases in one.)

## New

- **AI out of the box.** The ISO now ships with a starter model baked in — the assistant
  answers from the very first boot, with **no internet connection at all**. Bigger models
  matched to your GPU download in the background while you're already talking. No more
  "installed an AI OS, got no AI."
- **Full-duplex conversation.** Talk over him — he stops mid-sentence within a fraction of a
  second and listens. Follow-ups chain naturally without repeating the wake word. Acoustic
  echo cancellation keeps him from hearing himself through your speakers.
- **"Repeat that."** He re-speaks his last reply verbatim — and short-term conversational
  memory means "tell me more" and "what was that called?" finally work.
- **A real chat window.** The Chat app now has a "Just chat" mode — a private, local
  ChatGPT-style conversation with any installed model (pick from a dropdown), saved locally,
  nothing routed to agents, nothing leaving your machine.
- **ThorAI Settings.** A settings window for how Jarvis behaves ("open ThorAI settings"):
  choose how chatty confirmations are (**Full / Simple / Off** — questions, problems, and
  answers are always spoken in full), toggle interruptions, pick your search engine.
- **AMD Radeon support, out of the box.** ThorOS now detects Radeon VRAM and picks the right
  model tier — verified live on an RX 7900 XTX: full-GPU inference with zero driver setup
  (AMD needs no proprietary driver, no activation reboot). ThorOS is GPU-vendor-neutral.

## Fixed

- First-boot model choice on AMD machines (previously fell back to the smallest model).
- Interrupted or cut-off replies can always be recovered with "repeat that", including
  pending yes/no questions.

## Already running ThorOS?

Say **"update yourself"** — existing installs get full-duplex, the chat mode, ThorAI
Settings, and the conversational memory via self-update. (The baked starter model and the
audio-layer echo cancellation are part of the new ISO; fresh installs get those.)

`ThorOS-1.4-amd64.iso` — sha256 on the release page.
