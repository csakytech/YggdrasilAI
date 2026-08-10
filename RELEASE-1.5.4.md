# ThorOS 1.5.4 — see what she heard, and say it your own way

Two things make an assistant feel like a helper rather than a machine you have to learn: it
understands you when you phrase something its way, and when it gets you wrong you can *see* why.
This release is mostly those two ideas.

## New

- **A Conversation window.** Everything you said, as it was heard, and everything the assistant
  replied — scroll back through the last few exchanges. When an answer looks wrong, this tells
  you instantly whether you were misheard or misunderstood, which are very different problems.
  Off by default (it records what you say, so it's your call) — turn it on in
  **ThorAI Settings › Conversation log**, then open **Conversation** from the apps menu.
- **You can see which mode you're in.** A small badge sits on screen while a special mode like
  Development Mode is running, and it tells you how to leave. Modes capture what you say, so
  being in one without knowing is how people end up with baffling answers.
- **Change your desktop background by voice.** "Set my wallpaper to the sunset photo", or just
  "change my background" and it'll tell you what pictures it can see. It understands "the sunset
  one" — you don't have to know the filename.

## Better

- **Leave a mode however you say it.** "Exit development mode", "close it", "leave", "get out
  of it", "forget this project" — all work. Getting *out* of something should never depend on
  guessing the one magic phrase.
- **Ask what mode you're in** — "what mode are you in?" — and get a straight answer.
- **"Close all open windows"** works, along with phrasings nobody thought to teach it: "get rid
  of all this stuff", "shut the browser one". When a request doesn't match anything it knows, it
  now looks at what's *actually* on your screen and works out what you meant, instead of
  searching for an app called "all".
- **It asks before downloading.** Screen vision needs a 3 GB model; it now explains what that
  buys you and waits for a yes, instead of quietly using your connection. And if you have no
  internet it says so, rather than claiming a download is under way.
- **It won't guess.** When nothing it can do actually matches your request, it says so and helps
  in conversation, instead of firing off the nearest-looking action. Asking "how do I change the
  background?" gets you an answer, not a surprise.

## Fixed

- **Renaming actually sticks.** "Call yourself Amy" — and the Dashboard, chat, settings, task
  list and mission window all call her Amy. Several corners kept saying Jarvis, and one of them
  told you to say "Jarvis" to wake her, which by then no longer worked.
- More ways of renaming are understood — "I want you to be known as Amy from now on" used to be
  ignored while "change your name to Amy" worked.
- Questions about your internet-facing IP address are answered properly instead of being taken
  as something else.

## Already running ThorOS?

Say **"update yourself"**. Everything here arrives that way — there's no need to reinstall.

*No new ISO for this one: the download stays 1.5.3, and it updates itself to 1.5.4 on first run.*
