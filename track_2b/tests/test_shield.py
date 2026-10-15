"""Unit tests: no model, no network. Run with `make test`."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from shield.apertus import ApertusClient  # noqa: E402
from shield.audit import AuditLog  # noqa: E402
from shield.ner import locate, parse_entities  # noqa: E402
from shield.proxy import Shield, create_app  # noqa: E402
from shield.swiss import Span, ahv_ean13, ch_iban, ch_phone, ch_uid, find_identifiers  # noqa: E402
from shield.tokens import PLACEHOLDER_RE, TokenMap, merge, repair  # noqa: E402

DOSSIER = json.loads((pathlib.Path(__file__).resolve().parents[1] / "data" / "swiss_dossier.json").read_text("utf-8"))


# ── validators ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["756.2468.1357.91", "756.1357.9246.83", "7568642097534"])
def test_ahv_valid(value):
    assert ahv_ean13(value)


@pytest.mark.parametrize("value", ["756.2468.1357.92", "755.2468.1357.91", "756.2468.1357"])
def test_ahv_invalid(value):
    assert not ahv_ean13(value)


def test_iban():
    assert ch_iban("CH03 0070 0110 0001 2345 6")
    assert not ch_iban("CH04 0070 0110 0001 2345 6")
    assert not ch_iban("DE89 3704 0044 0532 0130 00")


def test_uid():
    assert ch_uid("CHE-468.135.796")
    assert not ch_uid("CHE-468.135.797")


def test_phone():
    assert ch_phone("+41 79 123 45 67")
    assert ch_phone("+41 (0)44 123 45 67")
    assert ch_phone("021 555 01 23")
    assert not ch_phone("+41 012 345 67")


def test_dossier_structured_values_all_found():
    found = {s.text for s in find_identifiers(DOSSIER["document"])}
    expected = {v["text"] for v in DOSSIER["sensitive_values"] if v["detector"] == "regex"}
    assert expected <= found


def test_random_numbers_left_alone():
    text = "Rechnung 7561234567890, Betrag CHF 12 345.60, Termin 31.03.2026, Kundennr. 0441234567"
    assert find_identifiers(text) == []


def test_keyword_anchored_value_only():
    spans = find_identifiers("passaporto n. X1234567 e Geburtsdatum: 14.03.1981")
    assert {(s.type, s.text) for s in spans} == {("PASSPORT", "X1234567"), ("DATE_OF_BIRTH", "14.03.1981")}


# ── NER parsing ─────────────────────────────────────────────────────────────────

def test_parse_entities_json():
    raw = json.dumps({"entities": [{"type": "PERSON", "text": "Lea Muster"}, {"type": "BOGUS", "text": "x"}]})
    assert parse_entities(raw) == [{"type": "PERSON", "text": "Lea Muster"}]


def test_parse_entities_salvages_truncated_reply():
    raw = '{"entities": [{"type": "PERSON", "text": "Lea Muster"}, {"type": "ORG", "text": "Muster Treuhand GmbH"}, {"type": "ADD'
    assert [e["text"] for e in parse_entities(raw)] == ["Lea Muster", "Muster Treuhand GmbH"]


def test_locate_drops_invented_text():
    spans = locate("Mandantin Lea Muster", [{"type": "PERSON", "text": "Lea Muster"}, {"type": "PERSON", "text": "Max"}])
    assert [(s.start, s.end) for s in spans] == [(10, 20)]


# ── placeholders ────────────────────────────────────────────────────────────────

def test_same_value_same_token_and_restore():
    tm = TokenMap()
    text = "IBAN CH03 0070 0110 0001 2345 6 und nochmals CH03 0070 0110 0001 2345 6"
    redacted, counts = tm.redact(text, find_identifiers(text))
    tokens = PLACEHOLDER_RE.findall(redacted)
    assert len(tokens) == 2 and tokens[0] == tokens[1] and counts == {"IBAN": 2}
    assert "CH03" not in redacted
    assert tm.restore(redacted) == (text, 0)


def test_merge_prefers_validated_identifier():
    a = Span(0, 10, "ADDRESS", "x" * 10, "apertus")
    b = Span(2, 6, "AHV", "x" * 4, "pattern")
    assert merge([a, b]) == [b]


def test_repair_degraded_forms_only_known_codes():
    tm = TokenMap()
    token = tm.token_for("AHV", "756.2468.1357.91")
    code = PLACEHOLDER_RE.fullmatch(token).group(2)
    text = f"AHV {code}; AHV_{code}; [AHV_{code}]; IBAN_{code}; AHV ffffff"
    fixed, n = repair(text, tm.known_codes())
    assert n == 3
    assert fixed == f"AHV {token}; {token}; {token}; IBAN_{code}; AHV ffffff"


def test_unknown_placeholder_is_counted_unresolved():
    assert TokenMap().restore("see [[AHV_abcdef]]") == ("see [[AHV_abcdef]]", 1)


# ── audit chain ─────────────────────────────────────────────────────────────────

def test_audit_chain_detects_edit_and_delete():
    log = AuditLog(key=b"k" * 32)
    for i in range(3):
        log.append(entity_counts={"AHV": i})
    assert log.verify()
    log.events[1]["entity_counts"] = {"AHV": 9}
    assert not log.verify()
    log.events[1]["entity_counts"] = {"AHV": 1}
    assert log.verify()
    del log.events[0]
    assert not log.verify()


# ── proxy end to end with a fake model ──────────────────────────────────────────

class FakeApertus(ApertusClient):
    """Answers with a degraded copy of the first placeholder it sees; NER finds the name."""

    async def chat(self, body):
        self.sent.append(body)
        user = body["messages"][-1]["content"]
        if "response_format" in body:
            return {"choices": [{"message": {"content": json.dumps({"entities": [{"type": "PERSON", "text": "Lea Muster"}]})}}]}
        m = PLACEHOLDER_RE.search(user)
        return {"choices": [{"message": {"content": f"Kundin {m.group(0) if m else '-'}, AHV {m.group(2) if m else ''}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_proxy_route_redacts_repairs_restores_and_audits():
    answer, ner, audit = FakeApertus(base_url="http://fake"), FakeApertus(base_url="http://fake"), AuditLog()
    app = create_app(Shield(answer, ner, audit))
    text = "Mandantin Lea Muster, AHV-Nr. 756.2468.1357.91"

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": text}]})

    resp = asyncio.run(run())
    assert resp.status_code == 200
    final = resp.json()["choices"][0]["message"]["content"]
    sent = json.dumps(answer.sent[-1], ensure_ascii=False)
    assert "Lea Muster" not in sent and "756.2468.1357.91" not in sent
    assert "Lea Muster" in final and "[[" not in final
    assert audit.verify() and audit.events[-1]["entity_counts"] == {"PERSON": 1, "AHV": 1}
    assert "Lea Muster" not in json.dumps(audit.events)


def test_label_words_are_never_entities():
    spans = locate("Numero AVS [[AHV_abcdef]], Conto: X, Ditta: Esempio Immobili SA",
                   [{"type": "ORG", "text": "AVS"}, {"type": "ORG", "text": "Conto"},
                    {"type": "ORG", "text": "Esempio Immobili SA"}, {"type": "ORG", "text": "[[AHV_abcdef]]"}])
    assert [s.text for s in spans] == ["Esempio Immobili SA"]


def test_chunks_split_paragraphs_and_long_lines():
    from shield.ner import CHUNK_CHARS, chunks
    text = "a b c\n\n" + ("line of text\n" * 200) + "\n\nend"
    parts = chunks(text)
    assert parts[0] == "a b c" and parts[-1] == "end"
    assert all(len(p) <= CHUNK_CHARS for p in parts)


def test_birth_date_without_year_is_dropped():
    spans = locate("Steuererklärung bis 31.03. einreichen, geboren 14.03.1981",
                   [{"type": "DATE_OF_BIRTH", "text": "31.03."}, {"type": "DATE_OF_BIRTH", "text": "14.03.1981"}])
    assert [s.text for s in spans] == ["14.03.1981"]
