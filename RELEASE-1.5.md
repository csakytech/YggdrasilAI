# ThorOS 1.5 — Jarvis can see your screen

Ask Jarvis what's on your screen and he tells you — a fully local, private set of eyes for your
computer. Point him at a webpage, an error message, a photo, a form, and say "what am I looking
at?" He takes a screenshot, looks at it with a vision model running on your own machine, and
describes it aloud. Nothing leaves your computer.

## New

- **Screen vision.** "What am I looking at?", "read the screen", "what does this error say?",
  "what's on my screen?" — Jarvis captures the screen and describes it, reads text aloud, or
  answers a specific question about what's shown. Verified reading a number straight off a
  calculator, an error message verbatim, the contents of a page.
- **Private by construction.** The screenshot and the vision model both stay on your machine.
  Your screen is never uploaded anywhere.
- **Built for accessibility.** For someone who can't see the screen well, an assistant that
  reads and *understands* what's on it — not just the text, but what's happening — is the point
  of ThorOS.

The first time you ask Jarvis to look, he downloads his vision model (about 3 GB, once) — he'll
tell you and be ready in a few minutes. After that it's instant, and it works offline.

This is deliberately **look, don't touch**: Jarvis sees and describes, he doesn't click. Letting
him act on what he sees is the next step, and it will always ask first.

## Also

- Everything from 1.4.1 (ask about your machine — local/external IP, memory, CPU, graphics
  card; reboot/shutdown by voice) is included.

## Already running ThorOS?

Say **"update yourself"** — screen vision arrives automatically. (The vision model downloads
the first time you ask Jarvis to look.)

`ThorOS-1.5-amd64.iso` — sha256 on the release page.
