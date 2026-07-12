# ThorOS 1.2 — ask for software, and it's installed

ThorOS 1.2 turns "I want to make a video, what should I install?" into a finished job: Jarvis
researches live, recommends the right program, asks once, installs it from the Debian
repositories, and offers to open it. No browser tabs, no package names, no terminal.

## New

- **Software by voice.** "Install OBS Studio" — Jarvis resolves the spoken name to the real
  Debian package, always confirms aloud before touching anything, installs it, and tells you
  how to open it. Recommendations flow straight into the same offer: research → recommend →
  "would you like me to install it?" → done.
- **No login, no screen lock.** A hands-free OS shouldn't park a password prompt in front of
  people who can't type one. After first-time setup the machine boots to a ready desktop and
  never locks you out. (Re-enable locking any time in Settings → Privacy; your password still
  protects administrative actions.)
- **First reply in seconds, not minutes.** The AI model is warmed into the GPU at boot —
  previously the first command after a restart could sit silent for over a minute while the
  model loaded.
- **A restart that announces itself.** On NVIDIA machines, first-time setup installs the
  graphics driver and restarts once to activate it — now with a desktop notification and a
  30-second countdown in the Welcome window, so it never takes you by surprise.

## Fixed

- "Open Google and search for X" now actually searches (a browser startup race silently
  dropped the search half).
- Web search defaults to DuckDuckGo: Google presents CAPTCHA challenges to ThorOS's
  voice-driven browser, and a CAPTCHA is a locked door for hands-free users. (Prefer Google
  anyway? Set `search_engine` in `~/.config/yggdrasil/config.json`.)
- Release migrations now actually run on self-updates — a permissions oversight had silently
  skipped every migration since v0.8. Updating to 1.2 also applies all of the above to
  existing installs, including the boot-time preload and no-lock defaults.
- Everything from the 1.1 emergency fix (sudo membership repair, NVIDIA driver activation
  with the correct model tier for your GPU) is included and has now been verified end-to-end
  on real hardware.

## Under the hood

- New Software agent: strict package-name validation end to end — spoken input can never
  reach the shell; installs run through a single-purpose root helper, always behind a spoken
  yes/no.
- Development builds (never published) now record a full QA transcript of every exchange —
  three of this release's fixes were found by replaying real sessions from it.
- 88-test suite green.

`ThorOS-1.2-amd64.iso` — sha256 on the release page.
