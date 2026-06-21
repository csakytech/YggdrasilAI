# Yggdrasil OS — Architecture

This is the living design document. It captures **how** Yggdrasil works and **why** the
key decisions were made. Phase 1 is the thin vertical slice; everything here is built so
later phases (multi-agent teams, marketplace) slot in without a rewrite.

## 1. The shape of the system

```
            voice ──┐
                    ├──► Orchestrator ──► Planner (local LLM, schema-constrained)
            text ───┘          │
                               ▼
                         Bus (asyncio now → NATS later)
                               │
        ┌──────────┬───────────┼───────────┬──────────┐
        ▼          ▼           ▼           ▼          ▼
   File Agent  Browser Ag.  Coding Ag.  Doc Agent  Image Agent   ...
        │
        ▼
   PermissionManager  ◄── the ONLY component that authorizes OS-affecting actions
        │                  (dangerous actions → authorization-code challenge)
        ▼
   Debian / Hardware
```

The user states a **goal**. The Orchestrator asks the Planner for a short, concrete plan,
then dispatches each step to an agent over the Bus. Every agent checks with the
PermissionManager before touching anything. Dangerous actions pause for an authorization
code. Results flow back and are spoken/printed.

## 2. Request lifecycle (the spine)

1. **Input** — wake word + speech, or a typed prompt. (Phase 0/1: text works today.)
2. **Plan** — `Planner.plan(goal)` returns a list of `Task`s. The LLM is constrained to a
   JSON schema whose `action` field is an *enum of the actually-available tools*, so it
   cannot invent tools or emit malformed JSON. A no-model `HeuristicPlanner` covers the
   File Agent verbs so the spine runs without Ollama.
3. **Dispatch** — `bus.request("file", task)` routes to the agent registered for that domain.
4. **Authorize** — the agent calls `PermissionManager.check`. Safe → proceed. Dangerous →
   a 6-digit code is minted and presented; the task is *parked, not executed*.
5. **Confirm** — the user says/types `Authorize 710628`. The manager verifies (constant-time,
   single-use, TTL-bound) and issues a short-lived `auth_token` bound to that exact action.
   The orchestrator re-dispatches the task carrying the token; the agent now executes.
6. **Respond** — a `Result` (OK / DENIED / TIMEOUT / ERROR) is rendered back to the user.

## 3. Components

| Component | File | Responsibility |
|---|---|---|
| **Bus** | `core/bus.py` | Transport-agnostic message passing. `LocalBus` (asyncio) now; `NatsBus` later. Defines `Task` / `Result` / `Status`. |
| **PermissionManager** | `core/permissions.py` | Sole authorizer. Capability manifests, policy, authorization-code challenge + verification. |
| **LLM** | `core/llm.py` | `LLMProvider` interface, `OllamaProvider` (local, schema-constrained), VRAM→model tier table. Cloud providers slot in here later. |
| **Orchestrator** | `core/orchestrator.py` | Goal → plan → dispatch → authorize → results. `HeuristicPlanner` + `LLMPlanner`. |
| **BaseAgent** | `agents/base.py` | Declares capabilities, enforces the permission gate, returns structured results. |
| **FileAgent** | `agents/file_agent.py` | Phase-1 agent: `create_folder` (safe), `delete` (dangerous). Sandbox-jailed. |
| **Voice** | `voice/loop.py` | Wake → VAD endpoint → STT → orchestrator → TTS state machine (Phase 1, stubbed). |
| **CLI** | `cli.py` | Text-mode entrypoint. Runs the whole spine today. |

## 4. Key decisions (ADRs in brief)

**ADR-0001 — Debian base, ISO built from a recipe.** We do not fork the kernel or write
drivers. Debian 13 "trixie" is the foundation. The installable `.iso` is a *build artifact*
produced reproducibly from a `live-build` recipe in git (`yggdrasil-iso/`), **not** a
hand-snapshotted machine. Rationale: reproducible, auditable, version-controlled, no baked-in
secrets — you don't ship software by zipping your computer; you compile from source.

**ADR-0002 — Local-first, model-agnostic, tiered.** Everything runs offline on local
hardware by default via Ollama. Agents depend only on `LLMProvider`, never on a concrete
model. First boot detects VRAM and selects a model tier, so the *same ISO* runs on a laptop
or a 3060 box. A cloud/API tier is an **optional, user-enabled** upgrade behind the same
interface — never required, never the default. (Default brain: Qwen3 family.)

**ADR-0003 — asyncio bus now, NATS later.** Phase 1 is one box and must ship with no moving
parts → in-process asyncio behind a `Bus` interface. The multi-agent-team phase swaps in
NATS (subjects, queue groups, request/reply, single static binary, ISO-friendly) by
implementing the same interface — agents and orchestrator are untouched. Heavy/dangerous
work runs in subprocess workers so a crash never takes down the bus.

**ADR-0004 — Permission by design; the AI cannot self-authorize.** The AI emits `Task`s
only. The `PermissionManager` is the single authorizer. Dangerous capabilities route through
an authorization challenge: a 6-digit, single-use, TTL-bound code the user must speak or
type. The resulting token is bound to the specific action and consumed on use. Challenge
state never enters the LLM context, so a confused or adversarial model cannot approve itself.

## 5. Model tiers (drives first-boot detection)

| Detected VRAM | Default model | Notes |
|---|---|---|
| 24 GB+ | `qwen3:32b` | Best agentic tier |
| 16 GB | `qwen3:14b` | Planner resident + small worker |
| 12 GB (RTX 3060) | `qwen3:14b` | Reference tier; drop to `qwen3:8b` if running voice+image concurrently |
| 6–8 GB | `qwen3:8b` | Single model |
| CPU-only | `llama3.2:3b` | Degraded; not real-time — user is warned |

Tags are `[VERIFY]` at build time (the model landscape moves fast). The table lives in
`core/llm.py` (`MODEL_TIERS`) and is mirrored by `yggdrasil-iso` first-boot logic.

## 6. What ships in the ISO

One `live-build` recipe, two editions via a `YGG_EDITION` flag:
- **online / lean (~3–4 GB):** voice models bundled (always-offline voice); the LLM is pulled
  on first boot for the detected tier.
- **offline / bundled (~9–10 GB):** the default LLM is baked into the squashfs for air-gapped
  installs.

See `../yggdrasil-iso/README.md` for the build + VM-test commands.

## 7. Status & non-goals

**Now (Phase 0):** runnable text spine — `create a folder` / `delete` through the full
plan → permission → agent path, including the authorization-code flow. **Not yet:** real
voice loop, LLM planner tuning, the other agents, packaging the `.deb`, a built ISO.

**Non-goals (ever):** replacing the Linux kernel, writing drivers, rewriting filesystems,
competing with Windows/Linux as an OS. Debian is the engine; Yggdrasil is the intelligence.
