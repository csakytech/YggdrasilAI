# Yggdrasil OS

> Debian becomes the engine. Yggdrasil becomes the intelligence.

An **AI-first, local-first operating environment** built on Debian Linux and shipped as an
installable `.iso`. Instead of opening applications and driving them by hand, you state a
**goal** — by voice (custom wake word, e.g. "Jarvis…") or a typed prompt — and an orchestrator
plans the work and dispatches a team of permissioned agents that carry it out, asking for
confirmation only on dangerous actions.

```
User → Goal → AI (plan) → Permissioned Agents → Result
```

## Status

🌱 **Phase 0 — Foundations.** Architecture and project scaffold in progress. Nothing runs yet.
See [ROADMAP.md](ROADMAP.md) for the plan and `docs/adr/` for the decisions behind it.

## Principles

- **Local first.** Everything runs offline on local hardware by default (Ollama + small models
  on the GPU). Cloud/API models are an *optional, user-enabled* upgrade — never required.
- **Model-agnostic & tier-aware.** Agents talk to a local model endpoint and pick a model size
  from detected hardware, so the same ISO runs on a modest laptop or a 3060 box.
- **Permission by design.** The AI never touches the OS directly. Every action goes through an
  agent with declared capabilities; destructive actions require an authorization code.
- **The OS is compiled, not copied.** The `.iso` is a reproducible build artifact generated from
  a `live-build` recipe in git — not a hand-snapshotted machine.
- **Debian does the OS work.** No new kernel, drivers, or filesystems. We build the intelligence
  layer on a stable foundation.

## Repository layout (planned)

| Path | What |
|---|---|
| `yggdrasil/`     | The application — orchestrator, agents, voice loop (Python) |
| `yggdrasil-iso/` | The distribution recipe — `live-build` config that produces the `.iso` |
| `docs/`          | Architecture, decision records (ADRs), build/run guides |

## Reference hardware (the dev box)

NVIDIA RTX 3060 12GB · Intel i7-3770 (4c/8t) · 16→32GB RAM · 1TB SSD · Debian (native).
The 3060's 12GB VRAM is the design target for the default local model tier.

## Author Note
Growing up watching movies like Wargames with it's WOPR computer, HAL 9000 and more, it has always
been a dream of mine to create an OS run by Agents known as FusionAI. Having studied Neural networks,
general AI and algorithmic computations in my early age, I have always been exited to try to bring
life into AI (AGI). My system is not built from the ground up, instead I have a base Linux distro
running the hardware layer. The Agents run throughout the system with full control over the system
itself all the way down the layers. 
