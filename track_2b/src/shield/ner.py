"""Names, companies and addresses found by Apertus itself, inside the perimeter.

Swiss identifiers with check digits are caught by :mod:`shield.swiss`. Free-text personal
data (people, companies, street addresses) needs a language model. The same local Apertus
that answers the request reads the raw text first and returns the entities as JSON; the raw
text therefore never leaves the machine, even for this step.

Two details make an 8B model dependable here:
  * one worked example in the prompt;
  * a JSON schema in ``response_format``: llama.cpp and vLLM compile it into a grammar, so
    the output is always valid JSON, and ``maxItems`` stops a repetition loop from running
    into ``max_tokens``. A reply that was cut off anyway is salvaged item by item.
Any failure returns an empty list: the NER pass must never break the request.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .apertus import ApertusClient
from .swiss import Span

PLACEHOLDER_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)_([0-9a-f]{6,12})\]\]")

NER_TYPES = ["PERSON", "ORG", "ADDRESS", "DATE_OF_BIRTH"]

SYSTEM_PROMPT = (
    "You find personal and confidential data in client documents written in German, French, "
    "Italian or English. Return ONLY a JSON object of the form "
    '{"entities": [{"type": <TYPE>, "text": <exact substring>}]}. '
    "Types: PERSON (full names of people), ORG (companies, banks, authorities), ADDRESS "
    "(street and number, postcode and town), DATE_OF_BIRTH (a date that is a person's birth date). Copy each entity's text EXACTLY as it appears in "
    "the source. List every person, company and address in order of appearance and mention "
    "each distinct text only once. Skip anything already written as a [[TYPE_hex]] token. "
    "Field labels are not entities: AHV, AVS, IBAN, UID, IDE, IDI, MWST, TVA, IVA, Konto, "
    "Compte, Conto, Firma, Ditta, Société, Mandant, Client, Cliente. "
    'Do not invent entities and do not add commentary. If there are none, return {"entities": []}.'
)

EXAMPLE_USER = "Kundin: Anna Beispiel, Seestrasse 4, 3000 Bern, arbeitet bei Beispiel AG."
EXAMPLE_ANSWER = json.dumps(
    {"entities": [
        {"type": "PERSON", "text": "Anna Beispiel"},
        {"type": "ADDRESS", "text": "Seestrasse 4, 3000 Bern"},
        {"type": "ORG", "text": "Beispiel AG"},
    ]},
    ensure_ascii=False,
)

ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {"type": {"type": "string", "enum": NER_TYPES}, "text": {"type": "string"}},
                "required": ["type", "text"],
            },
        }
    },
    "required": ["entities"],
}

MAX_INPUT_CHARS = 20_000
_ITEM_RE = re.compile(r'\{\s*"type"\s*:\s*"(?P<type>[^"\\]+)"\s*,\s*"text"\s*:\s*"(?P<text>(?:[^"\\]|\\.)*)"\s*\}')


def build_request(text: str) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": EXAMPLE_USER},
            {"role": "assistant", "content": EXAMPLE_ANSWER},
            {"role": "user", "content": text[:MAX_INPUT_CHARS]},
        ],
        "temperature": 0.0,
        "max_tokens": 600,
        "response_format": {"type": "json_schema", "json_schema": {"name": "entities", "schema": ENTITY_SCHEMA}},
    }


def parse_entities(raw: str | None) -> list[dict[str, str]]:
    """Entities from the model reply; salvages complete items from a truncated reply."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        items = data.get("entities", []) if isinstance(data, dict) else []
    except ValueError:
        items = []
        for m in _ITEM_RE.finditer(raw):
            try:
                items.append({"type": m.group("type"), "text": json.loads(f'"{m.group("text")}"')})
            except ValueError:
                continue
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("type") in NER_TYPES and isinstance(it.get("text"), str):
            if it["text"].strip():
                out.append({"type": it["type"], "text": it["text"].strip()})
    return out


# Words that label a field in de/fr/it paperwork. An 8B model sometimes tags them as ORG,
# especially next to a placeholder; they carry no personal data, so they are never redacted.
LABEL_WORDS = {
    "ahv", "avs", "ahv-nr", "iban", "uid", "ide", "idi", "mwst", "tva", "iva", "konto", "compte", "conto",
    "firma", "ditta", "société", "societe", "entreprise", "mandant", "mandantin", "client", "cliente",
    "kundin", "kunde", "arbeitgeber", "arbeitgeberin", "employeur", "datore di lavoro", "treuhand",
    "lohnkonto", "numéro avs", "numero avs", "steuererklärung", "telefon", "téléphone", "telefono",
}


def is_label(text: str) -> bool:
    t = text.strip(" .:-,;()").lower()
    return t in LABEL_WORDS or (len(t) <= 5 and text.strip().isupper())


def locate(text: str, entities: list[dict[str, str]]) -> list[Span]:
    """Every occurrence of every entity text in ``text``. Texts the model invented are dropped."""
    spans: list[Span] = []
    for ent in entities:
        if is_label(ent["text"]) or PLACEHOLDER_RE.search(ent["text"]):
            continue
        if ent["type"] == "DATE_OF_BIRTH" and not re.search(r"\d{4}", ent["text"]):
            continue  # a deadline such as "31.03." is not a birth date
        for m in re.finditer(re.escape(ent["text"]), text):
            spans.append(Span(m.start(), m.end(), ent["type"], ent["text"], "apertus"))
    return spans


CHUNK_CHARS = 900


def chunks(text: str) -> list[str]:
    """Paragraphs, with long paragraphs split by line. An 8B model lists the entities of a
    short passage far more completely than those of a whole multi-client dossier."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) <= CHUNK_CHARS:
            out.append(para)
            continue
        buf = ""
        for line in para.splitlines(keepends=True):
            if buf and len(buf) + len(line) > CHUNK_CHARS:
                out.append(buf)
                buf = ""
            buf += line
        out.append(buf)
    return [c for c in out if c.strip()]


async def find_entities(client: ApertusClient, text: str) -> list[Span]:
    """Entities in ``text``, one NER call per chunk. Spans inside an existing placeholder are dropped."""
    found: list[dict[str, str]] = []
    for chunk in chunks(text):
        if not re.search(r"[A-Za-zÀ-ÿ]{3}", PLACEHOLDER_RE.sub("", chunk)):
            continue
        try:
            reply = await client.chat(build_request(chunk))
            found += parse_entities(reply["choices"][0]["message"]["content"])
        except Exception:  # noqa: BLE001 - NER must never break the request path
            continue
    taken = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
    return [s for s in locate(text, found) if not any(s.start < e and b < s.end for b, e in taken)]
