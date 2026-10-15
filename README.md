# Treuhand Shield

A privacy proxy that lets a Swiss fiduciary office (Treuhandbüro) use the Swiss open model
**Apertus** on client files. AHV numbers, IBANs, company UIDs, names and addresses are
replaced with reversible placeholders before the prompt reaches the model, the real values
are put back into the answer, and every request leaves a tamper-evident audit record. The
whole chain runs on one machine with no internet route.

Built for **Hack Apertus**, online hackathon 1–16 October 2026, **Track 2B: Own Project**.
The project lives in [`track_2b/`](track_2b/):

| Path | What it is |
| --- | --- |
| [`track_2b/README.md`](track_2b/README.md) | Track 2B challenge description (from the template) |
| [`track_2b/technical_report.md`](track_2b/technical_report.md) | Architecture, use of Apertus, evaluation, limitations |
| `track_2b/INNOVA_Report.pdf` | The technical report as PDF |
| [`track_2b/Makefile`](track_2b/Makefile) | `make run` = air-gapped end-to-end demo in Docker |
| [`track_2b/src/shield/`](track_2b/src/shield/) | The proxy |
| [`track_2b/src/demo.py`](track_2b/src/demo.py), [`eval_ch.py`](track_2b/src/eval_ch.py) | Demo with wire capture, evaluation on 30 generated notes |
| [`track_2b/data/`](track_2b/data/) | Synthetic dossier and evaluation set (no real data) |
| [`track_2b/docs/`](track_2b/docs/) | Results, transcript, screenshots |

## How it works

```
employee / any OpenAI client
   |  POST /v1/chat/completions
   v
Treuhand Shield (FastAPI, on-premise or air-gapped)
   1  Swiss identifiers, check-digit validated: AHV/AVS, CH/LI IBAN, UID/IDE/IDI,
      +41 phones, passport and ID-card numbers, birth dates (de/fr/it cues), e-mail
   2  local Apertus NER pass on the already-redacted text: people, companies, addresses
   3  value -> [[TYPE_hex]]   (keyed HMAC, same value -> same placeholder)
   4  redacted prompt  ------------------------------------------------+
   5  repair degraded placeholders, restore real values                 |
   6  audit event: counts only, HMAC hash chain                         |
                                                                        v
                         llama.cpp server: Apertus v1.5 8B (GGUF Q4_K_M)
```

## Run it

```bash
cd track_2b
make run      # downloads the Apertus GGUF once (5 GB), then runs air-gapped
make eval     # 30 generated client notes in German, French and Italian
make test     # unit tests, no model needed
make serve    # the proxy on http://127.0.0.1:8000/v1 for any OpenAI client
```

`LLM_BASE_URL`, `LLM_NAME` and `LLM_API_KEY` point the proxy at any OpenAI-compatible
Apertus endpoint (`make run-remote` for a hosted sovereign deployment).

## Licence

Code: Apache License 2.0 ([LICENSE](LICENSE)). Documentation: CC-BY-4.0. Data: CDLA-Permissive-2.0.
Team INNOVA (INNOVA CONSULT LTD, Ottawa).
