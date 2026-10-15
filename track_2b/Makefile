.PHONY: run run-remote serve eval test down

# Air-gapped end-to-end demo: fetch Apertus weights once, start llama.cpp on an internal
# network, run the synthetic Swiss dossier through the proxy. Exit code 0 = checks passed.
run:
	docker compose up -d --wait llm
	docker compose run --rm --build demo

# Same demo against a hosted Apertus endpoint: set LLM_BASE_URL, LLM_NAME, LLM_API_KEY.
run-remote:
	docker compose --profile remote run --rm --build demo-remote

# The proxy as a service on http://127.0.0.1:8000/v1 (OpenAI chat API), audit log in out/.
serve:
	docker compose --profile serve up -d --build --wait shield

# 30 generated de/fr/it client notes: leak rate (patterns vs patterns + Apertus NER) and task fidelity.
eval:
	docker compose up -d --wait llm
	docker compose run --rm --build demo python src/eval_ch.py 30

# Unit tests (no model, no network).
test:
	docker compose run --rm --build --no-deps demo python -m pytest -q tests

down:
	docker compose --profile serve --profile remote down
