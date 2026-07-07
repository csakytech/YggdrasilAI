"""Voice manager — the assistant's voice is one Piper ``.onnx`` file, so voices are
swappable, downloadable, and previewable exactly like language models.

The active voice lives in user config (``~/.config/yggdrasil/config.json``, key ``voice``)
and ``voice/tts.py`` re-resolves it before every utterance — so "use the Ryan voice" takes
effect on the very next sentence, no restart. Voice files live in ``~/yggdrasil-voices/``
(the dir the ISO already uses). The CATALOG below is curated from Piper's free voice set
(rhasspy/piper-voices on Hugging Face) — original synthetic voices only. ThorOS does NOT
clone real people's voices (actors, celebrities): that needs the person's consent, so it's
out — for us and for marketplace personality packs later. A persona can *evoke* a character
through pace and manner, never through a cloned voice.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from . import config
from . import resolve as resolver

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

# Curated, known-good Piper voices. id format: <lang_REGION>-<name>-<quality>.
# label = what the user says ("use the Ryan voice"); blurb = shown in the picker.
CATALOG: dict[str, dict] = {
    "en_US-lessac-medium": {"label": "Lessac", "mb": 63,
                            "blurb": "Clear and neutral — the ThorOS default"},
    "en_US-ryan-high": {"label": "Ryan", "mb": 115,
                        "blurb": "Deep, confident male"},
    "en_US-joe-medium": {"label": "Joe", "mb": 63,
                         "blurb": "Relaxed, easy-going male"},
    "en_US-hfc_male-medium": {"label": "Calm male", "mb": 63,
                              "blurb": "Even and measured — precise, unhurried delivery"},
    "en_US-amy-medium": {"label": "Amy", "mb": 63,
                         "blurb": "Friendly, bright female"},
    "en_US-hfc_female-medium": {"label": "Calm female", "mb": 63,
                                "blurb": "Even and composed female"},
    "en_US-kristin-medium": {"label": "Kristin", "mb": 63,
                             "blurb": "Warm, gentle female"},
    "en_GB-alan-medium": {"label": "Alan", "mb": 63,
                          "blurb": "British male"},
    "en_GB-alba-medium": {"label": "Alba", "mb": 63,
                          "blurb": "Scottish female"},
}

_downloads: dict[str, dict] = {}  # voice id -> {"pct": float, "done": bool, "error": str|None}
_dl_lock = threading.Lock()


def voices_dir() -> Path:
    """Where downloads go (user-owned)."""
    return Path(os.environ.get("YGGDRASIL_VOICES_DIR") or (Path.home() / "yggdrasil-voices"))


def _search_dirs() -> list[Path]:
    """All places a voice may live: the download dir, the ISO's baked default
    (/opt/yggdrasil/voices), and wherever the launcher env points."""
    dirs = [voices_dir(), Path("/opt/yggdrasil/voices")]
    env = os.environ.get("YGGDRASIL_VOICE_MODEL")
    if env:
        dirs.append(Path(env).expanduser().parent)
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def path_for(vid: str) -> Path:
    """The existing file for a voice (searched everywhere), else its download target."""
    for d in _search_dirs():
        p = d / f"{vid}.onnx"
        if p.is_file():
            return p
    return voices_dir() / f"{vid}.onnx"


def url_for(vid: str) -> str:
    """HF layout: <lang>/<lang_REGION>/<name>/<quality>/<id>.onnx"""
    region, name, quality = vid.split("-", 2)
    return f"{HF}/{region.split('_')[0]}/{region}/{name}/{quality}/{vid}.onnx"


def installed() -> list[str]:
    out: set[str] = set()
    for d in _search_dirs():
        try:
            out.update(p.stem for p in d.glob("*.onnx"))
        except OSError:
            pass
    return sorted(out)


def active_path() -> Optional[str]:
    """The current voice file: config choice -> env fallback -> any installed voice."""
    vid = config.get_voice()
    if vid:
        p = path_for(vid)
        if p.is_file():
            return str(p)
    env = os.environ.get("YGGDRASIL_VOICE_MODEL")
    if env and Path(env).expanduser().is_file():
        return str(Path(env).expanduser())
    have = installed()
    return str(path_for(have[0])) if have else None


def active_id() -> Optional[str]:
    p = active_path()
    return Path(p).stem if p else None


def set_active(vid: str) -> None:
    config.set_voice(vid)


def label(vid: str) -> str:
    return (CATALOG.get(vid) or {}).get("label") or vid.replace("-", " ")


def _spoken_keys() -> dict[str, str]:
    ids = sorted(set(installed()) | set(CATALOG))
    by_key: dict[str, str] = {}
    for vid in ids:
        by_key[vid.lower()] = vid
        by_key[label(vid).lower()] = vid
        by_key[vid.split("-")[1].replace("_", " ")] = vid  # bare name: "ryan", "hfc male"
    return by_key


def _clean_spoken(spoken: str) -> str:
    return (spoken or "").strip().lower().removeprefix("the ").removesuffix(" voice").strip()


def resolve_spoken(spoken: str) -> Optional[str]:
    """'ryan' / 'the calm male voice' / 'alba' -> a catalog/installed voice id.

    Deliberately FORGIVING: switching a voice is trivially reversible (unlike deleting a
    file), and STT mangles names ("calm mail"), so when the strict resolver isn't sure we
    still take the clearly-best fuzzy candidate rather than dead-ending the user."""
    spoken = _clean_spoken(spoken)
    if not spoken:
        return None
    by_key = _spoken_keys()
    got, confident, _ = resolver.resolve(spoken, list(by_key), list(by_key))
    if got and confident:
        return by_key[got]
    ranked = _ranked(spoken)
    if ranked and ranked[0][1] >= 0.6 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 0.08):
        return ranked[0][0]
    return None


def _ranked(spoken: str) -> list[tuple[str, float]]:
    """Voice ids ranked by best fuzzy similarity to the spoken name."""
    import difflib

    best: dict[str, float] = {}
    for key, vid in _spoken_keys().items():
        r = difflib.SequenceMatcher(None, spoken, key).ratio()
        if r > best.get(vid, 0.0):
            best[vid] = r
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


def closest(spoken: str, n: int = 2) -> list[str]:
    """The n most likely intended voices — for 'did you mean…' replies."""
    return [vid for vid, score in _ranked(_clean_spoken(spoken))[:n] if score >= 0.45]


def download_status() -> dict[str, dict]:
    with _dl_lock:
        return {k: dict(v) for k, v in _downloads.items()}


def start_download(vid: str, on_done=None) -> None:
    """Fetch <id>.onnx + its required .onnx.json sidecar in the background."""
    with _dl_lock:
        if vid in _downloads and not _downloads[vid].get("done"):
            return
        _downloads[vid] = {"pct": 0.0, "done": False, "error": None}

    def worker() -> None:
        import httpx

        error = None
        try:
            voices_dir().mkdir(parents=True, exist_ok=True)
            url = url_for(vid)
            for suffix, weight in ((".onnx", 0.97), (".onnx.json", 0.03)):
                dest = voices_dir() / f"{vid}{suffix}"
                tmp = dest.with_suffix(dest.suffix + ".part")
                with httpx.Client(timeout=None, follow_redirects=True) as client:
                    with client.stream("GET", url.replace(".onnx", suffix) if suffix != ".onnx" else url) as r:
                        r.raise_for_status()
                        total = int(r.headers.get("content-length") or 0)
                        got = 0
                        base = 0.0 if suffix == ".onnx" else 97.0
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_bytes(1 << 16):
                                f.write(chunk)
                                got += len(chunk)
                                if total:
                                    with _dl_lock:
                                        _downloads[vid]["pct"] = base + 100.0 * weight * got / total
                tmp.replace(dest)
        except Exception as e:  # noqa: BLE001
            error = str(e)
            for suffix in (".onnx.part", ".onnx.json.part"):
                try:
                    (voices_dir() / f"{vid}{suffix}").unlink()
                except OSError:
                    pass
        with _dl_lock:
            _downloads[vid]["done"] = True
            _downloads[vid]["error"] = error
            if not error:
                _downloads[vid]["pct"] = 100.0
        if on_done:
            try:
                on_done(vid, error)
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True, name=f"voice-dl-{vid}").start()


SAMPLE = ("Hi — this is how I'd sound. I can open your apps, write documents, "
          "run commands, and keep your day on track.")


def preview(vid: str, delay: float = 2.0) -> bool:
    """Speak the sample line in a voice WITHOUT switching to it (separate process; a small
    delay so it doesn't talk over the assistant's own reply)."""
    p = path_for(vid)
    if not p.is_file():
        return False
    try:
        subprocess.Popen([sys.executable, "-m", "yggdrasil.voice.tts",
                          "--delay", str(delay), str(p), SAMPLE],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def open_picker() -> bool:
    """Open the Voices window (the 'see the voices and decide for yourself' panel)."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    try:
        subprocess.Popen([sys.executable, "-m", "yggdrasil.ui.voices"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
