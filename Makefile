PORT ?= 8000

.PHONY: setup run test lint typecheck demo

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.lock
	.venv/bin/pip install -e . --no-deps

run:
	.venv/bin/uvicorn search_intelligence.main:app --host 127.0.0.1 --port $(PORT)

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy src

demo:
	.venv/bin/python scripts/demo.py
