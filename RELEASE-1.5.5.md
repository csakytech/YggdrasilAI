# ThorOS 1.5.5 — choose the mind behind the machine

This release is about *your* models. ThorOS has always run its AI on your own hardware; now you
can see which model does each job and swap in your own — a coding model, a writing model, a
security model — and install one just by naming it.

## New

- **A Models screen.** ThorAI Settings → **Choose models…** — pick which model handles each job:
  routing, reasoning, coding, writing, seeing. Any model you've installed can fill any role, and
  the change takes effect on your next request. Install a specialist and put it exactly where it
  belongs.
- **Install a model by voice, from HuggingFace.** Say *"download the Dolphin Cyber model from
  HuggingFace"* and ThorOS finds the real model, picks a sensible version, and offers to download
  it — no typing long addresses. Or give it an exact address and it just installs.
- **It learns what's normal (optional).** A new setting, **"Learn what's normal on this
  computer,"** quietly notes which programs run and what your machine connects to, so it can one
  day tell you when something genuinely new appears — like an unknown program opening a network
  port. It watches for weeks before it says anything, it's off until you turn it on, and nothing
  ever leaves your machine. This is the groundwork for a security helper.

## Better

- **It answers instead of gesturing.** *"Which models do I have?"* now names them out loud
  instead of just counting them; the same for your voices and which model handles which job.
- **Downloads are honest.** A model download now shows up when you ask *"how's it going,"* and
  ThorOS tells you out loud when it finishes — or if it couldn't, instead of failing in silence.
- **It won't invent an answer.** A question it doesn't understand no longer comes back as a
  random report on your memory and system load — it says it isn't sure and helps instead.

## Included from 1.5.4

- **See what it heard** — the Conversation window shows what you said and what it replied, so a
  wrong answer tells you whether you were misheard or misunderstood (Settings → Conversation log).
- **A badge shows which mode you're in**, and you can leave a mode however you phrase it.
- **Change your desktop background by voice**, and **"close all open windows"** understands what
  you mean even when you say it a new way.

## Already running ThorOS?

Say **"update yourself."** After it updates, **log out and back in** to finish loading the new
version.

`ThorOS-1.5.5-amd64.iso` — sha256 on the release page.
