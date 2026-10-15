#!/usr/bin/env python3
"""Treuhand Shield end-to-end demo on a synthetic Swiss client dossier (de/fr/it).

  employee request -> POST /v1/chat/completions on the proxy (real FastAPI route, in-process)
     1. Swiss identifiers with check digits     (AHV, CH-IBAN, UID, +41 phones, passport, DOB, e-mail)
     2. local Apertus NER pass                  (names, companies, addresses)
     3. value -> [[TYPE_hex]] placeholder
  -> Apertus answers the redacted prompt (llama.cpp server, OpenAI-compatible API)
  -> placeholders repaired and restored, one hash-chained audit event

The demo proves the claims instead of asserting them:
  * every byte sent to the answering model is captured and searched for each of the 26
    synthetic sensitive values (a leaked structured identifier fails the run);
  * placeholders in the answer are counted and checked for restore;
  * the audit chain is verified, then one event is edited and verification must fail;
  * an egress probe shows whether the process can reach the internet at all.

Usage:  LLM_BASE_URL=http://localhost:8080/v1 python src/demo.py
Exit code 0 = no structured identifier leaked and the audit chain behaves.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
import socket
import sys
import time

import httpx

HERE = pathlib.Path(__file__).resolve().parent
TRACK = HERE.parent
sys.path.insert(0, str(HERE))

from shield.apertus import ApertusClient  # noqa: E402
from shield.audit import AuditLog  # noqa: E402
from shield.proxy import Shield, create_app  # noqa: E402
from shield.tokens import PLACEHOLDER_RE  # noqa: E402

DATA = TRACK / "data" / "swiss_dossier.json"
OUT_DIR = pathlib.Path(os.environ.get("DEMO_OUT_DIR", TRACK / "out"))
TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


def head(title: str) -> None:
    print()
    print(_c("1;36", f"== {title} " + "=" * max(0, 74 - len(title))))


def ok(msg: str) -> None:
    print(_c("32", "  [ok]   ") + msg)


def bad(msg: str) -> None:
    print(_c("31", "  [FAIL] ") + msg)


def info(msg: str) -> None:
    print("         " + msg)


def egress_probe() -> dict[str, str]:
    out = {}
    for host, port in (("huggingface.co", 443), ("1.1.1.1", 443)):
        try:
            socket.create_connection((host, port), timeout=3).close()
            out[f"{host}:{port}"] = "reachable"
        except OSError as exc:
            out[f"{host}:{port}"] = f"blocked ({type(exc).__name__})"
    return out


async def wait_for_model(client: ApertusClient, timeout_s: int = 900) -> str:
    deadline, last = time.monotonic() + timeout_s, ""
    while time.monotonic() < deadline:
        try:
            return await client.served_model()
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        await asyncio.sleep(3)
    raise SystemExit(f"model server at {client.base_url} not ready after {timeout_s}s ({last})")


def make_shield(audit: AuditLog) -> tuple[Shield, ApertusClient, ApertusClient]:
    answer = ApertusClient()
    ner = ApertusClient(base_url=os.environ.get("NER_BASE_URL", answer.base_url),
                        model=os.environ.get("NER_NAME", answer.model), api_key=answer.api_key)
    return Shield(answer, ner, audit), answer, ner


async def main() -> int:
    dossier = json.loads(DATA.read_text(encoding="utf-8"))
    sensitive = dossier["sensitive_values"]
    audit = AuditLog()
    shield, answer, ner = make_shield(audit)

    head("1. Runtime")
    info(f"answering model : {answer.model} at {answer.base_url}")
    info(f"NER model       : {ner.model} at {ner.base_url}")
    egress = egress_probe()
    for target, state in egress.items():
        info(f"egress {target:<20}: {state}")
    airgapped = all(v.startswith("blocked") for v in egress.values())
    (ok if airgapped else info)("no route to the internet: running air-gapped" if airgapped
                                else "internet reachable from this process (use `make run` for the air-gapped network)")
    served = await wait_for_model(answer)
    ok(f"model server ready, serving '{served}'")

    head("2. Client request (what the fiduciary employee pastes)")
    print(dossier["document"])
    request = {
        "model": "default", "temperature": 0.2, "max_tokens": 450,
        "messages": [
            {"role": "system", "content": "Du bist ein sorgfältiger Assistent eines Schweizer Treuhandbüros. Antworte knapp."},
            {"role": "user", "content": dossier["task"] + "\n\n" + dossier["document"]},
        ],
    }
    t0 = time.perf_counter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(shield)),
                                 base_url="http://shield.local", timeout=1800) as client:
        resp = await client.post("/v1/chat/completions", json=request)
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        bad(f"proxy returned HTTP {resp.status_code}: {resp.text[:300]}")
        return 1
    final = resp.json()["choices"][0]["message"]["content"]
    sent = answer.sent[-1]
    sent_text = json.dumps(sent, ensure_ascii=False)
    redacted_doc = sent["messages"][-1]["content"]
    raw_answer = shield.last.raw_answer

    head("3. What Apertus received (captured on the wire)")
    print(redacted_doc)
    head("4. Apertus answer, as generated (placeholders only)")
    print(raw_answer)
    head("5. Answer returned to the employee (values restored by the proxy)")
    print(final)

    head("6. Checks")
    leaks = [v for v in sensitive if v["text"] in sent_text]
    regex_vals = [v for v in sensitive if v["detector"] == "regex"]
    llm_vals = [v for v in sensitive if v["detector"] == "llm"]
    regex_leaks = [v for v in leaks if v["detector"] == "regex"]
    llm_leaks = [v for v in leaks if v["detector"] == "llm"]
    if regex_leaks:
        bad(f"{len(regex_leaks)} structured identifiers reached the model: " + ", ".join(v["type"] for v in regex_leaks))
    else:
        ok(f"{len(regex_vals)}/{len(regex_vals)} structured identifiers (AHV, IBAN, UID, phone, passport, DOB, "
           "e-mail) were replaced before leaving the proxy")
    caught = len(llm_vals) - len(llm_leaks)
    (ok if not llm_leaks else info)(f"{caught}/{len(llm_vals)} names, companies and addresses caught by the local Apertus NER pass")
    for v in llm_leaks:
        info(f"  missed by NER: {v['type']}")
    ev = audit.events[-1]
    placeholders_out = PLACEHOLDER_RE.findall(raw_answer)
    ok(f"Apertus wrote {len(placeholders_out)} tokens verbatim and {ev['placeholders_repaired']} in a degraded form "
       f"that the proxy repaired; {ev['placeholders_unresolved']} left unresolved in the final answer")
    restored = [v["text"] for v in sensitive if v["text"] in final]
    info(f"real values visible to the employee in the final answer: {len(restored)}")
    info(f"end-to-end latency: {elapsed:.1f}s (identifiers + NER + answer on {served})")

    head("7. Audit trail (hash-chained, counts only)")
    for e in audit.events:
        shown = {k: e[k] for k in ("route", "model", "entity_counts", "placeholders_repaired",
                                   "placeholders_unresolved", "latency_ms")}
        print("  " + json.dumps(shown, ensure_ascii=False, sort_keys=True))
        info(f"prev_hash  {e['prev_hash'][:16]}...  event_hash {e['event_hash'][:16]}...")
    audit_text = json.dumps(audit.events, ensure_ascii=False)
    audit_leaks = [v for v in sensitive if v["text"] in audit_text]
    chain_ok = audit.verify()
    (ok if chain_ok else bad)(f"audit chain verifies: {chain_ok}")
    (ok if not audit_leaks else bad)(f"audit log contains {len(audit_leaks)} raw sensitive values (expected 0)")
    original = copy.deepcopy(audit.events[0])
    counts = dict(original["entity_counts"]) or {"PERSON": 1}
    first = sorted(counts)[0]
    counts[first] = max(0, counts[first] - 1)  # an insider hiding one disclosure
    audit.events[0]["entity_counts"] = counts
    tampered_ok = audit.verify()
    (ok if not tampered_ok else bad)(f"after editing one audit event, chain verifies: {tampered_ok}")
    audit.events[0] = original

    report = {
        "model": served, "endpoint": answer.base_url, "egress": egress, "airgapped": airgapped,
        "latency_s": round(elapsed, 1),
        "structured_identifiers": len(regex_vals), "structured_leaks": len(regex_leaks),
        "ner_values": len(llm_vals), "ner_caught": caught, "ner_missed_types": [v["type"] for v in llm_leaks],
        "placeholders_in_answer": len(placeholders_out),
        "placeholders_repaired": ev["placeholders_repaired"], "placeholders_unresolved": ev["placeholders_unresolved"],
        "audit_chain_valid": chain_ok, "audit_chain_detects_tamper": not tampered_ok,
        "audit_raw_value_leaks": len(audit_leaks), "audit_events": audit.events,
        "redacted_prompt": redacted_doc, "model_answer_raw": raw_answer, "answer_final_synthetic": final,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "demo_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    head("Done")
    info(f"report: {OUT_DIR / 'demo_report.json'}")
    passed = not regex_leaks and chain_ok and not tampered_ok and not audit_leaks
    (ok if passed else bad)("demo checks passed" if passed else "demo checks failed")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
