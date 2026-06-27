# ThorOS Agent Template

This folder is a complete, working **agent packet** — the unit you build and publish to the ThorOS
marketplace. Copy it, rename it, replace the logic, and you have your own agent.

```
agent-template/
├── manifest.toml     # the contract: identity, routing, capabilities, PERMISSIONS, deps, entry point
├── notes_agent.py    # the code: an Agent subclass with one method per capability
├── icon.png          # optional, shown in the marketplace
└── README.md         # this file
```

## How an agent works (the lifecycle)

1. **Declare** — `manifest.toml` says who you are, which `domain` you handle, your `capabilities`
   (verbs), and exactly which `permissions` you need (files, network, commands, apps).
2. **Install** — ThorOS verifies the signature, shows the user a plain-language **consent screen**
   built from your manifest, and (on approval) drops the packet into
   `~/.local/share/yggdrasil/modules/<id>/`.
3. **Route** — your `planner_examples` teach the on-device planner which spoken phrasings map to
   `your-domain.verb`.
4. **Run** — ThorOS checks the capability and the user's permission, then calls
   `_execute(verb, params)`. You return `{"speech": "..."}` — what Jarvis says back.

> **Profiles decide what's _active_.** Installed ≠ active. One agent per domain is active at a time,
> so the local model's tool-menu stays small and routing stays reliable.

## Build your own — change these four things

1. **`manifest.toml`** → set `[agent].id` (`yourname.thing`), `[routing].domain`, the
   `[[capability]]` list, and `[routing].planner_examples`.
2. **`notes_agent.py`** → rename the class, match `domain` / `module_id` / `capabilities` /
   `planner_examples` to the manifest, and replace the handler methods with your logic.
3. **`[permissions]`** → declare the **least** you need (see below).
4. **`[entrypoint]`** → point `module`/`class` at your file and class.

Import only from the stable SDK — **`from yggdrasil.sdk import Agent, Capability`** — never from
`yggdrasil.core.*` internals (those can change; the SDK won't, within an `API_VERSION`).

## The security model — read this

An agent is **code that runs on the user's machine and acts**. So:

- **Least privilege.** Every agent gets a *private* data dir for free (no permission needed). Only
  request `filesystem_*`, `network`, `run_commands`, or `controls_apps` if you genuinely need them —
  each one makes the install scarier and the review stricter.
- **Declared = enforced.** Community agents run **sandboxed**; you can only do what the manifest
  declares and the user approved. Don't bother trying to reach outside it.
- **Dangerous = gated.** Mark destructive verbs `dangerous = true`. By the time your method runs,
  the user has already authorized it (spoken/typed auth code) — you don't handle that yourself.

## Test it locally (developer mode)

```
thoros agent install ./agent-template --dev      # sideload unsigned, for development (warns loudly)
thoros agent remove yourname.notes-example
```
Then talk to Jarvis: *"make a note to call mom"*, *"read my notes"*, *"summarize my notes"*.

## Publish

```
thoros agent sign ./agent-template               # sign with your key
thoros agent publish ./agent-template            # opens a PR to the marketplace index
```
On submission the registry runs **automated checks** (manifest valid, builds, lint, static analysis,
permission scan, malware scan). Agents are then listed in a **tier**:

- **Community** — passed the automated gate, runs sandboxed, labelled *unreviewed*. Users opt in.
- **Verified / Official** — additionally **reviewed by the Thor team** and signed; higher trust,
  fewer sandbox restrictions.

> `--dev`, `thoros agent ...`, and the publish flow are the intended tooling; the runtime contract
> (this manifest + Agent class) is the part that's concrete today.
