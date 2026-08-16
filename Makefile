.PHONY: install format-rust format-python format-all lint-rust lint-python lint-frontend lint-all test-rust test-python test-all check

PY := .venv/bin/python

install:
	uv sync
	cd frontend && npm install

# ------------- formatting --------------
format-rust:
	cargo fmt

format-python:
	uvx ruff format python tests examples

format-all: format-rust format-python

# ------------- linting ---------------
lint-rust:
	cargo clippy --all-targets -- -D warnings

lint-python:
	uvx ruff check --select E9,F python tests examples

lint-frontend:
	cd frontend && npx tsc --noEmit

lint-all: lint-rust lint-python lint-frontend

# ------------- testing ---------------
test-rust:
	cargo test

test-python:
	PYTHONPATH=python $(PY) -m pytest tests/

test-all: test-rust test-python

check: format-all lint-all
