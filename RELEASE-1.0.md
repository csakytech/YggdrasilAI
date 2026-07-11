# ThorOS 1.0

**An AI-first, local-first operating system you run by voice.**

Talk to your computer in plain language and it gets real work done — opening apps, writing and
editing documents, browsing the web, running commands, building software — entirely on your own
machine. Private by default, your choice of local or cloud. Built on Debian, open source, and
designed for everyone, including people who can't use a keyboard or mouse.

This is the first stable release: the version to install, share, and build on.

## What ThorOS does

- **Talk to it.** Say the wake word and speak. Wake-word → speech-to-text → an on-device planner
  works out what you mean → specialized agents carry it out → it replies in a natural voice. All
  of it runs on your hardware; unplug the internet and it still works.
- **It does real work, not just chat.** Agents open programs, create and edit documents (bold,
  lists, find-and-replace, export to PDF), manage files (always confirming before it deletes),
  run terminal commands, search the web, set reminders, and give you spoken briefings.
- **Browse the web by voice.** Say "click" to number every link and button on the page, then
  "select 4" to open one — or "read the page" to have it read aloud. Real hands-free browsing,
  built for people who can't use a mouse.
- **Smart Help — "what can I say here?"** Say "Jarvis, help" anywhere and a card shows the exact
  commands that work *right where you are* — in Firefox, a word processor, your files, or
  Development Mode. Every command is numbered: just say "do number 3" to run it.
- **Development Mode.** "Jarvis, enter development mode," describe what you want to build in your
  own words, answer a few questions, and AI agents scaffold and build runnable software with you.
- **It remembers.** "What was I working on yesterday?" — ThorOS keeps a private, on-device
  activity journal and can recap your day.
- **Many models, your choice.** Bind different local LLMs to different jobs (planner, coder,
  writer, reasoner); run several at once if your machine can handle it.
- **It explains itself.** Ask "why did you do that?" and it tells you the real reasoning behind
  its last action.
- **Built for every generation.** Hands-free login, spoken interaction, and a goal-oriented
  design so the computer works like a helpful person — not a machine you have to fight with.

## Private by default

The speech recognition, the language model, and the voice all run locally. Your documents, your
commands, and your conversations never leave the machine unless you choose a cloud model. No
tracking.

## Get it

- **Download the ISO**, write it to a USB stick, and boot it — try it live before installing.
- Already running ThorOS? It **updates itself** — you'll be offered 1.0 automatically, or just
  say "update".

## Under the hood

Debian + GNOME, a permissioned multi-agent runtime, Whisper (speech-to-text), Ollama (local
LLMs), and Piper (voice) — with a first-boot that detects your GPU, picks a model tier, and
starts the assistant. Free and open source under Apache-2.0 / MIT.

Powered by Yggdrasil AI · [yggdrasilai.org](https://www.yggdrasilai.org)
