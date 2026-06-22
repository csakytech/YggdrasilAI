# Yggdrasil OS — Modules, Profiles & the Agent Ecosystem

This is the blueprint for how Yggdrasil grows: a generic **Core** that always works, **Modules**
that people install to add capabilities, and **Profiles** that let one machine be a Python
programmer this week and a science lab the next. It is both the *architecture* and the
*creator guideline*. Companion: [ARCHITECTURE.md](ARCHITECTURE.md) (the runtime), and the
agent contract in §5.

## 1. The layered model

```
┌──────────────────────────────────────────────┐
│ PROFILES  (modes you switch between)          │  "coding", "science", "default"
│   = persona + which agents are ACTIVE + prefs │
├──────────────────────────────────────────────┤
│ MODULES   (installed on disk, opt-in)         │  chef, research, trading, weather…
│   agents · personalities · teams              │  installed ≠ active
├──────────────────────────────────────────────┤
│ CORE / BASE SYSTEM  (always present)          │  Jarvis persona · File · Memory ·
│   ships in the ISO, generic, always works     │  System · Settings · onboarding · desktop
└──────────────────────────────────────────────┘
```

Two rules hold the model together:

1. **Installed ≠ Active.** A module on disk does nothing until a Profile turns it on. You may
   have 50 installed and 5 active.
2. **The active set stays small on purpose.** Small local models route reliably only with a
   short tool menu — so Profiles aren't just tidy, they're what keeps planning accurate.

## 2. The Core (base system)

What every install boots into before any add-ons — generic and always functional:

- **Core agents:** `file`, `memory`, `system` (OS/app control), `settings` (manage profiles,
  identity, active agents).
- **Generic personality** "Jarvis" + the Debian desktop.
- **First-boot onboarding:** name, wake word, voice, timezone. (Driven by the ISO first-boot
  hook; this is its UI.)

The Core ships in the ISO and is never uninstallable. Everything else is optional.

## 3. Modules — the unit of extension

Three kinds, increasing in complexity:

| Kind | Contains | Examples |
|---|---|---|
| **agent** | code (`BaseAgent` subclass) + manifest | weather, research, trading, home-automation |
| **sentinel** | an always-on background monitor that raises alerts unprompted | Security/Warden, backup watcher, crypto/price watcher |
| **personality** | data only (name, wake word, voice, persona prompt) | "Friday", "Sarcastic Butler" |
| **team** *(later)* | a bundle of agents + a manager + shared permissions | "Bob's Research Team" |

**Reactive vs Sentinel.** Most agents are *reactive* — the planner invokes them in response to a
goal. A **sentinel** is *proactive*: it runs a lightweight check on an interval and speaks up
when something changes (no LLM in the hot loop; the model is only called to triage a finding).
The Security Sentinel is the first; the same pattern powers any "tell me when X happens"
monitor (backups, prices, a server). See `core/sentinel.py`.

**A module is a folder with a manifest** — that's the whole definition. The manifest declares
everything the host needs to *list, gate, and route to* the module **without executing its
code**, so the install-consent screen is safe to show.

### Layout on disk

```
~/.local/share/yggdrasil/modules/<id>/      installed module (manifest + code + assets)
~/.local/share/yggdrasil/data/<id>/         that module's private, namespaced data
~/.config/yggdrasil/profiles/<name>.yaml    a profile
~/.config/yggdrasil/config.yaml             active profile, identity defaults
~/.config/yggdrasil/memory.json             long-term memory (per-profile later)
```

## 4. The manifest — `module.yaml`

```yaml
id: bob.weather            # namespaced: <author>.<name>, globally unique
kind: agent                # agent | personality | team
name: Weather
version: 0.1.0             # semver
author: Bob
description: Current conditions and forecasts.
license: MIT
min_yggdrasil: 0.2.0      # host-compat floor

domain: weather            # the verb-space this agent owns (one active per domain/profile)
entrypoint: weather:WeatherAgent      # python "module:Class"

capabilities:
  - { name: current,  dangerous: false, description: "Current weather for a place" }
  - { name: forecast, dangerous: false, description: "Multi-day forecast" }

permissions:               # declared, shown at install, enforced at runtime
  network: true            # or a domain allow-list
  filesystem: none         # none | workspace | home(consent) | [paths]
  subprocess: false

planner_examples:          # teaches the LLM to route here — host merges these in
  - "what's the weather in Paris -> weather.current(Paris)"
  - "will it rain tomorrow in Oslo -> weather.forecast(Oslo)"

requires: [ httpx ]        # python deps, installed into the module's own venv
```

Personality manifest is smaller (`kind: personality`, no code/domain):

```yaml
id: jane.friday
kind: personality
name: Friday
version: 1.0.0
wake_word: hey_jarvis          # bundled or a trained model file
voice: en_GB-jenny-medium      # a Piper voice id
persona: "You are Friday: warm, dry wit, brief. Call the user 'boss'."
```

## 5. The agent contract — the five rules for creators

1. **Subclass `BaseAgent`** — set `domain` + `capabilities`, implement
   `async _execute(verb, params)`. That is the entire code contract.
2. **Declare honestly** — every capability in the manifest, and flag `dangerous: true` for
   anything destructive/irreversible. The host gates those behind the spoken/typed
   **authorization code** automatically; you never implement auth yourself.
3. **Never touch the OS directly** — only via your declared capabilities and declared
   `permissions`. The host enforces scope (file sandbox, network on/off).
4. **Be model-agnostic & offline-friendly** — talk to the local LLM endpoint; never hardcode a
   model or bake in secrets/keys.
5. **Namespace + semver** — `id: author.name`, `version: MAJOR.MINOR.PATCH`.

A minimal agent:

```python
from yggdrasil.agents.base import BaseAgent
from yggdrasil.core.permissions import Capability

class WeatherAgent(BaseAgent):
    domain = "weather"
    capabilities = {
        "current":  Capability("current",  dangerous=False, description="Current weather"),
        "forecast": Capability("forecast", dangerous=False, description="Forecast"),
    }
    async def _execute(self, verb, params):
        place = params.get("argument", "")
        ...  # fetch + return a dict; the host speaks/saves it
```

## 6. Profiles — modes you switch between

A **Profile** is a saved bundle:

```yaml
# ~/.config/yggdrasil/profiles/coding.yaml
name: coding
persona: jane.friday            # a personality module (or inline)
active: [ file, memory, system, alice.python, bob.git ]   # active agents
preferences: { workspace: ~/Projects, verbosity: terse }
```

- *"Switch to coding mode"* → activates that agent set + persona instantly (no downloads).
- *"Reset to default"* → the generic base, nothing extra active.
- `default` profile ships with just the Core active.

Switching only changes **what's active and the persona** — installed modules and their data
stay put, so flipping back and forth is lossless and fast.

## 7. Lifecycle — install · update · uninstall · enable · disable · switch

| Action | What happens |
|---|---|
| **install** | fetch module → resolve deps into its own venv → **show consent** (capabilities + permissions, per the whitepaper screen) → place under `modules/<id>/`. Disabled by default. |
| **enable / disable** | toggle membership in the *current profile's* `active` list. Instant. |
| **update** | `bob.chef` 1.0 → 1.1: replace code in place, keep the module's data/config. |
| **uninstall** | remove the module dir; optionally keep or purge its `data/<id>/`. |
| **switch-profile** | swap the active set + persona to another profile. |

**Competing modules ("a better culinary chef").** `bob.chef@1.0` and `alice.chef@2.1` are
*different* modules (different `id`). Both may be installed. A profile activates **one per
`domain`** — the registry flags a domain clash and the user picks. **Nothing is ever
auto-deleted;** you choose which to activate and may uninstall the loser. Ratings/reviews (§9)
guide the choice. Each module's data lives under its own `data/<id>/`, so swapping chefs never
loses your saved recipes.

## 8. Permissions & trust

- **Consent at install:** the host renders the manifest's `capabilities` + `permissions` as a
  yes/no screen ("Internet ✓, Cannot delete system files ✗ → Install?").
- **Danger gating at runtime:** `dangerous: true` capabilities always route through the
  authorization-code challenge (already built — see ARCHITECTURE.md ADR-0004). The module can
  *request* but can never self-authorize.
- **Scope enforcement:** v1 = declaration + consent + the existing file sandbox + capability
  danger flags. Later = real isolation (subprocess workers, namespaces/seccomp, a network
  allow-list proxy) so a module physically cannot exceed its grant.
- **Provenance (marketplace):** modules are signed; the host verifies checksum/signature before
  install. Unsigned/local modules install with a clear warning.

## 9. Runtime — how modules load

A **registry** at startup:

1. Scans the Core agents + `modules/<id>/module.yaml` for the **active** profile.
2. (On first install) shows consent; on load, imports the entrypoint and instantiates
   `Agent(bus, perms, config)`, then `start()`.
3. **Assembles the planner from manifests** — `allowed_actions` and the few-shot
   `planner_examples` are merged from every *active* module. This is the key change: the
   planner is **data-driven**, so a new agent becomes usable purely by being active — **zero
   core code changes.**

## 10. Marketplace (future, Phase 4)

A simple index (a git repo or small service) of modules with versions, ratings, and reviews.
`yggdrasil install bob.weather` resolves → verifies signature → shows consent → installs.
Until then, "install" = from a local folder or a git URL. The on-device model (Core + Profiles
+ permissions) is identical regardless of where a module came from.

## 11. Status & build order

- [ ] **Plugin registry + manifest + data-driven planner** — File & Memory become the first
      modules (dogfood the spec). *Prerequisite for everything below.*
- [ ] **Profiles** — switch / reset / list; `default` ships.
- [ ] **Settings + onboarding agent** — set name/voice/wake word; manage profiles + active
      agents by voice ("call yourself Friday", "switch to coding mode", "what's active?").
- [ ] **Install / update / uninstall flow** + consent screen + the first downloadable agent.
- [ ] **System agent** (OS/app control) as a Core capability.
- [ ] **Marketplace** index + signing + ratings (Phase 4).

Core first, generic and working; then it grows — one consented, profile-scoped module at a time.
