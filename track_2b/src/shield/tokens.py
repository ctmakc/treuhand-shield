"""Reversible placeholders: value -> ``[[TYPE_hex]]`` -> value.

Within one session the same value always gets the same placeholder, so the model can still
reason about "the client" and "her IBAN". The code is a keyed HMAC of type and value; the
key is random per session and never leaves the process, so a placeholder reveals nothing
about the value. The mapping lives only in process memory for the session.

Apertus 8B sometimes writes a placeholder it was given in a degraded form (``AHV c4857b``,
``AHV_c4857b``, ``[AHV_c4857b]``). :func:`repair` rewrites such forms to the canonical
token, and only for codes the model was actually given, so it can never create a mapping
that did not exist.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

from .swiss import Span

PLACEHOLDER_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)_([0-9a-f]{6,12})\]\]")

INSTRUCTION = (
    "Personal data in this conversation is protected: each sensitive value was replaced by "
    "a token such as [[AHV_3f9a1c]] or [[IBAN_77e0d2]]. A token IS the value. Whenever you "
    "mention that value, write the complete token exactly as it appears, with the double "
    "square brackets, the type and the code. Correct: 'AHV-Nr. [[AHV_3f9a1c]]'. Wrong: "
    "'AHV 3f9a1c', 'AHV_3f9a1c', '[AHV_3f9a1c]'. Never invent new tokens and never ask for "
    "the hidden values."
)

_DEGRADED_RE = re.compile(
    r"(?<![\w\[])\[{0,2}(?:(?P<type>[A-Z][A-Z0-9_]*?)(?P<sep>[_ ]))?(?P<hex>[0-9a-f]{6,12})\]{0,2}(?![\w\]])"
)


def merge(spans: list[Span]) -> list[Span]:
    """Non-overlapping spans. Validated identifiers win over model findings, then longer wins."""
    ranked = sorted(spans, key=lambda s: (s.source != "pattern", -(s.end - s.start), s.start))
    kept: list[Span] = []
    for s in ranked:
        if all(s.end <= k.start or s.start >= k.end for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)


@dataclass
class TokenMap:
    key: bytes = field(default_factory=lambda: os.urandom(32))
    _by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    _by_token: dict[str, str] = field(default_factory=dict)

    def token_for(self, etype: str, value: str) -> str:
        known = self._by_value.get((etype, value))
        if known:
            return known
        digest = hmac.new(self.key, f"{etype}\x00{value}".encode(), hashlib.sha256).hexdigest()
        for n in (6, 8, 10, 12):
            token = f"[[{etype}_{digest[:n]}]]"
            if token not in self._by_token:
                break
        self._by_value[(etype, value)] = token
        self._by_token[token] = value
        return token

    def redact(self, text: str, spans: list[Span]) -> tuple[str, dict[str, int]]:
        """Replace every span; returns the redacted text and counts per entity type."""
        out, pos, counts = [], 0, {}
        for s in merge(spans):
            out.append(text[pos:s.start])
            out.append(self.token_for(s.type, text[s.start:s.end]))
            counts[s.type] = counts.get(s.type, 0) + 1
            pos = s.end
        out.append(text[pos:])
        return "".join(out), counts

    def known_codes(self) -> dict[str, tuple[str, str]]:
        """hex code -> (type, canonical token) for every token issued in this session."""
        return {m.group(2): (m.group(1), t) for t in self._by_token if (m := PLACEHOLDER_RE.fullmatch(t))}

    def restore(self, text: str) -> tuple[str, int]:
        """Put the real values back; returns the text and the number of tokens left unresolved."""
        unresolved = 0

        def sub(m: re.Match[str]) -> str:
            nonlocal unresolved
            value = self._by_token.get(m.group(0))
            if value is None:
                unresolved += 1
                return m.group(0)
            return value

        return PLACEHOLDER_RE.sub(sub, text), unresolved


def repair(text: str, known: dict[str, tuple[str, str]]) -> tuple[str, int]:
    """Rewrite degraded forms of known tokens to the canonical ``[[TYPE_hex]]`` form."""
    if not known or not text:
        return text, 0
    count = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal count
        hit = known.get(m.group("hex"))
        if hit is None or (m.group("type") is not None and m.group("type") != hit[0]):
            return m.group(0)
        count += 1
        # "AHV c4857b" reads as a label plus a value: keep the label.
        if m.group("type") and m.group("sep") == " " and not m.group(0).startswith("["):
            return f"{m.group('type')} {hit[1]}"
        return hit[1]

    out, pos = [], 0
    for canon in PLACEHOLDER_RE.finditer(text):
        out.append(_DEGRADED_RE.sub(sub, text[pos:canon.start()]))
        out.append(canon.group(0))
        pos = canon.end()
    out.append(_DEGRADED_RE.sub(sub, text[pos:]))
    return "".join(out), count
