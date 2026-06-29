"""Resolve a spoken, approximate reference to a real filesystem entry.

Jarvis can read the true names off the disk, so the user shouldn't have to pronounce them exactly.
Given what the user said and the actual entries in a directory, pick the intended one through a ladder:
exact → case/space/punctuation-insensitive → ordinal ("number 3", "the last one") → prefix → substring
→ fuzzy (for STT slips). Returns (name | None, confident, candidates) — `confident` is False for fuzzy
matches and ambiguity, so callers can confirm before doing anything destructive.
"""
from __future__ import annotations

import difflib
import re

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
# Filler words to strip so "the reports folder" matches an entry named "Reports".
_FILLER = re.compile(r"\b(the|a|an|my|that|this|please|folder|file|directory|dir|one|item|named|called)\b", re.I)


def _clean(s: str) -> str:
    return _FILLER.sub(" ", s or "").strip()


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _ordinal_index(spoken: str, n: int):
    """Index into a list of n items if `spoken` is purely a position reference, else None."""
    s = _clean(spoken).lower().strip()
    if s in ("last", "final"):
        return n - 1 if n else None
    m = re.fullmatch(r"\d+", s) or re.fullmatch(r"number\s*(\d+)", s) or re.fullmatch(r"(\d+)(?:st|nd|rd|th)", s)
    if m:
        i = int(m.group(1) if m.groups() else m.group(0)) - 1
        return i if 0 <= i < n else None
    if s in _ORDINALS:
        i = _ORDINALS[s] - 1
        return i if 0 <= i < n else None
    return None


def resolve(spoken: str, entries: list[str], ordered: list[str] | None = None):
    """entries: candidate names (any order). ordered: names as last listed (for ordinal refs).
    Returns (match | None, confident: bool, candidates: list)."""
    spoken = (spoken or "").strip()
    if not spoken or not entries:
        return None, False, []
    if spoken in entries:                                   # 1. exact
        return spoken, True, [spoken]
    order = ordered if ordered else sorted(entries)         # 2. ordinal ("the third one")
    idx = _ordinal_index(spoken, len(order))
    if idx is not None:
        return order[idx], True, [order[idx]]
    want = _norm(_clean(spoken))                            # 3. normalized exact (case/space/punct/filler)
    if not want:
        return None, False, []
    norm_hits = [e for e in entries if _norm(e) == want]
    if len(norm_hits) == 1:
        return norm_hits[0], True, norm_hits
    if len(norm_hits) > 1:
        return None, False, norm_hits
    pre = [e for e in entries if _norm(e).startswith(want)]  # 4. prefix, then substring
    sub = [e for e in entries if want in _norm(e)]
    for hits in (pre, sub):
        if len(hits) == 1:
            return hits[0], True, hits
        if len(hits) > 1:
            return None, False, hits
    close = difflib.get_close_matches(want, [_norm(e) for e in entries], n=3, cutoff=0.7)  # 5. fuzzy
    if close:
        cand = [e for e in entries if _norm(e) in close]
        return (cand[0], False, cand) if len(cand) == 1 else (None, False, cand)
    return None, False, []
