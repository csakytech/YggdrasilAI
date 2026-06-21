# Yggdrasil OS — Roadmap

Phases mirror the whitepaper, each with a concrete, demoable definition of done.

## Phase 0 — Foundations (now)
Scaffold both repos, lock the architecture, prove the toolchain.
- [ ] `yggdrasil/` app skeleton (orchestrator, BaseAgent, File Agent, CLI loop)
- [ ] `yggdrasil-iso/` live-build skeleton that produces a bootable Debian ISO
- [ ] Local LLM reachable via Ollama with a VRAM → model tier table
- [ ] ADRs for the foundational decisions
- **Done when:** `lb build` produces an ISO that boots in a VM, and the app runs the Phase-1 loop on the dev box.

## Phase 1 — Jarvis Prototype
The thin vertical slice that exercises the whole stack.
- [ ] Wake word ("Jarvis…") + always-listening with conversation endpointing
- [ ] STT (Whisper) → planner (local LLM) → File Agent → TTS ("Done.")
- [ ] Permission manager + authorization-code confirmation for dangerous ops
- [ ] Typed-prompt mode as a fallback to voice
- **Done when:** "Jarvis, create a folder called Crypto Research." → folder exists → "Done."

## Phase 2 — Autonomous Assistant
- [ ] Browser Agent (search, read, summarize)
- [ ] Document Agent (reports, PDFs, embedded images)
- [ ] Coding Agent (write / run / fix)
- [ ] Image Agent (local SDXL)
- [ ] Persistent memory
- **Done when:** "Research AI GPUs and make a report." → a real document with charts and images.

## Phase 3 — Multi-Agent Teams
- [ ] Manager/planner that fans work out to parallel agents
- [ ] Shared memory + task coordination over the message bus
- **Done when:** one request spins up a coordinated team that returns a combined result.

## Phase 4 — Community Marketplace
- [ ] Agent packaging + sandboxed permission manifests
- [ ] Install / rate / review flow ("install capabilities, not software")
- **Done when:** "Install Bob's Research Team." shows its agents + permissions and installs safely.
