# yggdrasil (the application)

The Yggdrasil OS agent runtime: orchestrator + permissioned agents + (Phase-1) voice loop.
This is plain Python and runs anywhere; the real target is the Debian box. It is later
packaged as a `.deb` and folded into the ISO (`../yggdrasil-iso/`).

## Run the Phase-0 spine (works today, no model needed)

```bash
cd yggdrasil
python -m yggdrasil
```

Then try:

```
you > create a folder called Crypto Research
jarvis > Done.

you > delete Crypto Research
  [AUTH REQUIRED] file.delete Crypto Research
  To approve, type:  Authorize 481920
  > Authorize 481920
jarvis > Done.
```

Folders are created inside a **sandbox** (`~/YggdrasilSandbox` by default; override with
`YGGDRASIL_SANDBOX`). The File Agent cannot touch anything outside it.

## Use the real LLM planner (on the Debian box, with Ollama)

```bash
ollama serve &
ollama pull qwen3:8b
YGGDRASIL_MODEL=qwen3:8b python -m yggdrasil
```

## Tests

```bash
pip install -e .[dev]
pytest
```

## Layout

```
src/yggdrasil/
  core/         bus.py (asyncio→NATS), permissions.py (auth codes), llm.py (Ollama+tiers),
                orchestrator.py (plan→dispatch→authorize)
  agents/       base.py (capability gate), file_agent.py (create_folder / delete)
  voice/        loop.py (Phase-1 stub: wake→VAD→STT→act→TTS)
  cli.py        text-mode entrypoint
```

See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the design and decisions.
