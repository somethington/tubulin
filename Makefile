.PHONY: install wheel format-rust format-python format-all lint-rust lint-python lint-frontend lint-all test-rust test-python test-frontend test-all check

PY := .venv/bin/python

install:
	uv sync --group examples
	cd frontend && npm install

# ------------- packaging -------------
# One wheel carrying the Rust binary, the Python package, and the built frontend, so
# `pip install tubulin` needs neither a Rust toolchain nor node.
wheel:
	cd frontend && npm ci && npm run build
	rm -rf python/tubulin/_frontend
	cp -R frontend/dist python/tubulin/_frontend
	uvx maturin build --release --out dist
	@echo "built:" && ls -la dist/*.whl

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
	PYTHONPATH=python $(PY) -m pytest tests/ examples/tpp/test_tpp.py

test-frontend:
	cd frontend && npm test

test-all: test-rust test-python test-frontend

check: format-all lint-all
