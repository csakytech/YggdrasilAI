# ThorOS 1.5.3 — he can hear you with the internet unplugged

ThorOS has always promised that your assistant runs on your own machine. It turns out that was
only half true: the AI that *thinks* was built into every ISO, but the part that *listens* quietly
downloaded itself from the internet the first time you spoke. On a fresh machine that meant a long
silent wait — and on a machine with no internet at all, an assistant that could never hear you.

Now the ears ship in the box too. Unplug the network cable, install ThorOS, and talk to it.

## New

- **Speech recognition works offline, from the first boot.** The speech model is built into the
  image alongside the AI and the voice. Nothing to download, nothing to wait for.
- **Your computer starts ready to listen.** ThorOS now signs in hands-free on the *first* start,
  not the second — so someone who can't use a keyboard is never left at a login screen they
  can't get past.
- **You choose how you sign in.** The welcome screen now asks: start straight to the desktop, or
  require your password every time. Hands-free is the default, but it's your machine.

## Fixed

- **Jarvis could fail to start on a new install** and stay silent until you started him by hand.
  He was trying to fetch the speech model before the network was awake, and gave up. Now there's
  nothing to fetch.
- **The status strip never appeared.** The little "Thinking…" indicator has been shipping in a
  state where it could never launch. It works now.
- **Assorted plumbing** — several helper programs shipped without permission to run. Fixed at the
  source so it can't happen again.

## Also

- Everything from 1.5.2 (click by sight, the Tasks window, honest progress reporting) and 1.5
  (screen vision) is included.

## Known issue

Installing with **no network cable connected** can stall on "Updating the list of available
packages". Install with the network connected — you can unplug it afterwards and everything,
including the voice, keeps working. A fix is coming.

## Already running ThorOS?

Say **"update yourself"** — you'll get the sign-in choice and the fixes. The built-in speech model
only applies to fresh installs from the ISO; your machine already downloaded its own the first
time you spoke to it, so nothing changes for you.

`ThorOS-1.5.3-amd64.iso` — sha256 on the release page.
