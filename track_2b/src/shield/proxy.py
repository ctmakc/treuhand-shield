"""OpenAI-compatible privacy proxy in front of Apertus.

  client -> POST /v1/chat/completions
    1. Swiss identifiers (AHV, IBAN, UID, phone, passport, ID card, birth date, e-mail)
    2. names, companies, addresses found by the local Apertus NER pass
    3. each value -> [[TYPE_hex]] placeholder, same value -> same placeholder
  -> Apertus answers the redacted prompt
  -> degraded placeholders repaired, real values restored, one audit event written

Any tool that speaks the OpenAI chat API can point at this proxy instead of the model.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .apertus import ApertusClient
from .audit import AuditLog
from .ner import find_entities
from .swiss import find_identifiers
from .tokens import INSTRUCTION, PLACEHOLDER_RE, TokenMap, repair


async def redact_text(text: str, tokens: TokenMap, ner: ApertusClient | None) -> tuple[str, dict[str, int]]:
    """Two passes. Validated identifiers are replaced first, so even the local NER model only
    sees them as placeholders; then Apertus finds names, companies and addresses in the rest."""
    redacted, counts = tokens.redact(text, find_identifiers(text))
    if ner is not None:
        redacted, more = tokens.redact(redacted, await find_entities(ner, redacted))
        for k, v in more.items():
            counts[k] = counts.get(k, 0) + v
    return redacted, counts


@dataclass
class ShieldResult:
    response: dict[str, Any]          # what the client gets back (values restored)
    sent_messages: list[dict[str, Any]]  # what the answering model received
    raw_answer: str                   # the model's answer before repair / restore
    counts: dict[str, int]
    repairs: int
    unresolved: int
    latency_ms: int


class Shield:
    def __init__(self, answer: ApertusClient, ner: ApertusClient | None, audit: AuditLog):
        self.answer, self.ner, self.audit = answer, ner, audit
        self.last: ShieldResult | None = None  # most recent request, for demos and evaluation

    async def _redact(self, text: str, tokens: TokenMap) -> tuple[str, dict[str, int]]:
        return await redact_text(text, tokens, self.ner)

    async def process(self, request: dict[str, Any]) -> ShieldResult:
        t0 = time.perf_counter()
        tokens = TokenMap()
        counts: dict[str, int] = {}
        messages = copy.deepcopy(request.get("messages") or [])
        for msg in messages:
            if isinstance(msg.get("content"), str):
                msg["content"], c = await self._redact(msg["content"], tokens)
                for k, v in c.items():
                    counts[k] = counts.get(k, 0) + v
        if any(PLACEHOLDER_RE.search(m.get("content") or "") for m in messages if isinstance(m.get("content"), str)):
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = f"{INSTRUCTION}\n\n{messages[0]['content']}"
            else:
                messages.insert(0, {"role": "system", "content": INSTRUCTION})

        body = {k: v for k, v in request.items() if k not in ("messages", "model", "stream")}
        body["messages"] = messages
        reply = await self.answer.chat(body)

        known = tokens.known_codes()
        raw_answer, repairs, unresolved = "", 0, 0
        for i, choice in enumerate(reply.get("choices") or []):
            content = (choice.get("message") or {}).get("content")
            if not isinstance(content, str):
                continue
            if i == 0:
                raw_answer = content
            fixed, n = repair(content, known)
            restored, left = tokens.restore(fixed)
            choice["message"]["content"] = restored
            repairs, unresolved = repairs + n, unresolved + left

        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = reply.get("usage") or {}
        self.audit.append(
            route="/v1/chat/completions", model=self.answer.model, entity_counts=counts,
            placeholders_repaired=repairs, placeholders_unresolved=unresolved,
            prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )
        self.last = ShieldResult(reply, messages, raw_answer, counts, repairs, unresolved, latency_ms)
        return self.last


def create_app(shield: Shield) -> FastAPI:
    app = FastAPI(title="Treuhand Shield", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": shield.answer.model, "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        payload = await request.json()
        if payload.get("stream"):
            raise HTTPException(400, "streaming is not supported yet; send stream=false")
        result = await shield.process(payload)
        return JSONResponse(result.response, headers={"x-shield-entities": str(sum(result.counts.values()))})

    return app
