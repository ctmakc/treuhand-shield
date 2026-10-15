"""Minimal client for Apertus behind any OpenAI-compatible server.

The local default is llama.cpp ``llama-server`` on GGUF weights; vLLM or a hosted Swiss
endpoint work the same way. Configuration uses the Hack Apertus variable names
``LLM_BASE_URL``, ``LLM_NAME`` and ``LLM_API_KEY``.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ApertusClient:
    base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://localhost:8080/v1"))
    model: str = field(default_factory=lambda: os.environ.get("LLM_NAME", "swiss-ai/Apertus-v1.5-8B"))
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    timeout_s: float = float(os.environ.get("LLM_TIMEOUT_S", "900"))
    # Every request body that leaves the process, in order. The demo searches these exact
    # bytes for sensitive values; production code can leave it unused.
    sent: list[dict[str, Any]] = field(default_factory=list)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        body = {**body, "model": self.model, "stream": False}
        self.sent.append(copy.deepcopy(body))
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url.rstrip('/')}/chat/completions", json=body, headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def served_model(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self.base_url.rstrip('/')}/models", headers=self._headers())
            r.raise_for_status()
            data = r.json().get("data") or [{}]
            return str(data[0].get("id", "?"))
