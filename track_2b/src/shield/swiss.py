"""Swiss identifiers found in fiduciary paperwork (de/fr/it), with check-digit validation.

Every numeric identifier that carries a published check digit is validated, so random digit
runs in a document (invoice numbers, amounts, dates) are left alone. Identifiers without a
check digit (passport, identity card, date of birth) are anchored on a keyword in German,
French or Italian, and only the value itself is replaced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# ── validators ──────────────────────────────────────────────────────────────────


def ahv_ean13(s: str) -> bool:
    """AHV/AVS social-security number 756.XXXX.XXXX.XC (13 digits, EAN-13 check digit)."""
    d = re.sub(r"[\s.\-]", "", s)
    if len(d) != 13 or not d.isdigit() or not d.startswith("756"):
        return False
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d[:12]))
    return (10 - total % 10) % 10 == int(d[12])


def iban_mod97(s: str) -> bool:
    """ISO 13616 check: move the first four characters to the end, letters to 10..35, mod 97."""
    v = re.sub(r"\s", "", s).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[0-9A-Z]+", v):
        return False
    digits = "".join(str(int(ch, 36)) for ch in v[4:] + v[:4])
    return int(digits) % 97 == 1


def ch_iban(s: str) -> bool:
    """Swiss or Liechtenstein IBAN: CH/LI, 21 characters, mod-97."""
    v = re.sub(r"\s", "", s).upper()
    return len(v) == 21 and v[:2] in {"CH", "LI"} and iban_mod97(v)


_UID_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)


def ch_uid(s: str) -> bool:
    """Enterprise number UID/IDE/IDI CHE-123.456.789: mod-11 check digit, 10 is never issued."""
    d = re.sub(r"[\s.\-]", "", s).upper().removeprefix("CHE")
    if len(d) != 9 or not d.isdigit():
        return False
    check = 11 - sum(int(c) * w for c, w in zip(d[:8], _UID_WEIGHTS)) % 11
    check = 0 if check == 11 else check
    return check != 10 and check == int(d[8])


def ch_phone(s: str) -> bool:
    """+41 / 0041 / 0 trunk prefix followed by a 9-digit national number not starting with 0."""
    v = re.sub(r"[\s.\-/]", "", s).replace("(0)", "")
    for prefix in ("+41", "0041", "0"):
        if v.startswith(prefix):
            national = v[len(prefix):]
            return len(national) == 9 and national.isdigit() and national[0] != "0"
    return False


# ── patterns ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pattern:
    type: str
    regex: re.Pattern[str]
    validator: Callable[[str], bool] | None = None


_I = re.IGNORECASE
_DOC_NO = r"(?:\s+(?:numéro|numero|Nr\.?|n[°o]?\.?))?[\s:#°]*"

PATTERNS: tuple[Pattern, ...] = (
    Pattern("AHV", re.compile(r"\b756[. ]?\d{4}[. ]?\d{4}[. ]?\d{2}\b"), ahv_ean13),
    Pattern("IBAN", re.compile(r"\b(?:CH|LI)\d{2}(?:[ ]?[0-9A-Z]{4}){4}[ ]?[0-9A-Z]\b"), ch_iban),
    Pattern("UID", re.compile(r"\bCHE[- ]?\d{3}\.?\d{3}\.?\d{3}\b"), ch_uid),
    Pattern("PHONE", re.compile(r"(?:\+41|\b0041)[ ]?(?:\(0\)[ ]?)?[1-9]\d[ ]?\d{3}[ ]?\d{2}[ ]?\d{2}\b"), ch_phone),
    # national form: separators required, so bare 10-digit numbers are not swept up
    Pattern("PHONE", re.compile(r"\b0[1-9]\d[ ]\d{3}[ ]\d{2}[ ]\d{2}\b"), ch_phone),
    Pattern("EMAIL", re.compile(r"\b[\w.+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b")),
    Pattern("PASSPORT", re.compile(
        r"\b(?:Reisepass|Passnummer|Pass(?:-?Nr\.?)?|passeport|passaporto)" + _DOC_NO + r"(?P<value>[A-Z]\d{7})\b", _I)),
    Pattern("NATIONAL_ID", re.compile(
        r"\b(?:Identitätskarte|ID-Karte|carte d'identité|carta d'identità)" + _DOC_NO + r"(?P<value>[A-Z]\d{7})\b", _I)),
    Pattern("DATE_OF_BIRTH", re.compile(
        r"(?:Geburtsdatum|geboren am|geb\.|date de naissance|née? le|data di nascita|nat[oa] il)\s*[:\-]?\s*"
        r"(?P<value>\d{1,2}\.\s?\d{1,2}\.\s?\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", _I)),
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    type: str
    text: str
    source: str  # "pattern" or "apertus"


def find_identifiers(text: str) -> list[Span]:
    """All validated Swiss identifiers and e-mail addresses in ``text``."""
    spans: list[Span] = []
    for p in PATTERNS:
        for m in p.regex.finditer(text):
            group = "value" if "value" in p.regex.groupindex else 0
            value, start, end = m.group(group), m.start(group), m.end(group)
            if p.validator is None or p.validator(value):
                spans.append(Span(start, end, p.type, value, "pattern"))
    return spans
