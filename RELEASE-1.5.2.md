# ThorOS 1.5.2 — Jarvis clicks what you name, and never pretends to be working

1.5 gave Jarvis eyes. This release gives him a hand — say "click the Watch Demo button" and he
finds it on screen and clicks it. And it makes him honest about work that takes a while: when he's
installing something you can see it happening, ask how it's going and get the real answer, and hear
him say when it's done.

## New

- **Click by sight.** "Click the Watch Demo button", "press the X", "scroll down" — the vision model
  finds the element you named and clicks it. He **never clicks blind**: if he can't find what you
  asked for, he says so and does nothing.
- **Looking is invisible.** Capturing the screen no longer makes a shutter sound or a white flash,
  so Jarvis can look without interrupting you.
- **A Tasks window.** Installing software no longer freezes the conversation — it runs as a tracked
  job in the background, and a window shows who's working, on what, how long it's been, and a live
  progress bar. It opens itself when work starts and tidies itself away when work is done.
- **"How's the install going?"** now reads the *real* state of the job. Jarvis reports what is
  actually happening, never a guess.
- **He tells you when it's finished.** "OBS Studio has finished installing" — spoken once, and
  honestly: a failure is announced as a failure, not dressed up as success.
- **"Did you mean OBS Studio?"** If he mishears an app name, he asks you to confirm the corrected
  name instead of silently swapping it for something else.

## Fixed

- **No invented progress.** Jarvis could previously claim he was "still trying to install it" when
  nothing was running. "I couldn't find that" is now a complete, honest answer on its own.
- **Spoken web addresses open instead of searching.** "Open up the yggdrasilai.org website" now goes
  to the site — filler words no longer knock a perfectly good address into a web search.
- **Looking at a dark screen.** ThorOS never locks, but the display can still blank — Jarvis now
  wakes it before looking, instead of faithfully describing a black rectangle.
- **The installer's network step.** On a machine that can't reach a DHCP server it could sit
  silently for minutes and look frozen to someone installing for the first time. The wait is now
  capped. *(Fresh installs from the ISO only.)*

## Also

- Everything from 1.5 (screen vision — "what am I looking at?") and 1.4.1 is included.

## Already running ThorOS?

Say **"update yourself"** — click-by-sight, the Tasks window, and the honesty fixes all arrive
automatically. The installer fix applies only to new installs from the ISO, so there's nothing to
reinstall.

`ThorOS-1.5.2-amd64.iso` — sha256 on the release page.
