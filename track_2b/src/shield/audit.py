"""Hash-chained audit log: one event per request, counts only, never values.

Each event stores the HMAC-SHA256 of its own canonical JSON plus the previous event's hash.
Editing, deleting or reordering any event after the fact breaks verification. The log can
be kept in memory (demo) or appended to a JSON-lines file (``AUDIT_LOG_PATH``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

GENESIS = "0" * 64


def _canonical(event: dict[str, Any]) -> bytes:
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


@dataclass
class AuditLog:
    key: bytes = field(default_factory=lambda: os.environ.get("AUDIT_HMAC_KEY", "").encode() or os.urandom(32))
    path: pathlib.Path | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, **fields: Any) -> dict[str, Any]:
        event = {"ts": round(time.time(), 3), **fields,
                 "prev_hash": self.events[-1]["event_hash"] if self.events else GENESIS}
        event["event_hash"] = hmac.new(self.key, _canonical(event), hashlib.sha256).hexdigest()
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def verify(self) -> bool:
        prev = GENESIS
        for ev in self.events:
            expected = hmac.new(self.key, _canonical(ev), hashlib.sha256).hexdigest()
            if ev.get("prev_hash") != prev or not hmac.compare_digest(expected, ev.get("event_hash", "")):
                return False
            prev = ev["event_hash"]
        return True
