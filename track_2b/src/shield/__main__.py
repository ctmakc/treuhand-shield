"""Run the proxy as a service: ``python -m shield`` (port 8000, or SHIELD_PORT)."""

import os
import pathlib

import uvicorn

from .apertus import ApertusClient
from .audit import AuditLog
from .proxy import Shield, create_app

answer = ApertusClient()
ner = None if os.environ.get("SHIELD_NER", "on") == "off" else ApertusClient(
    base_url=os.environ.get("NER_BASE_URL", answer.base_url),
    model=os.environ.get("NER_NAME", answer.model),
    api_key=os.environ.get("NER_API_KEY", answer.api_key),
)
log_path = os.environ.get("AUDIT_LOG_PATH")
app = create_app(Shield(answer, ner, AuditLog(path=pathlib.Path(log_path) if log_path else None)))

if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("SHIELD_HOST", "0.0.0.0"), port=int(os.environ.get("SHIELD_PORT", "8000")))
