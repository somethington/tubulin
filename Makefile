.PHONY: install wheel sdist stage-frontend build-frontend smoke-wheel smoke-sdist format-rust format-python format-frontend format-all lint-rust lint-python lint-frontend lint-all test-rust test-python test-frontend test-all audit-rust audit-frontend audit check

PY := .venv/bin/python

install:
	uv sync --group examples
	cd frontend && npm install

# ------------- packaging -------------
# One wheel carrying the Rust binary, the Python package, and the built frontend, so
# `pip install tubulin` needs neither a Rust toolchain nor node.
wheel: stage-frontend
	uvx maturin build --release --out dist
	@echo "built:" && ls -la dist/*.whl

# The sdist carries the built frontend too — a source build has no node, so without
# this a `pip install` from sdist compiles a runtime that serves 404 for every page.
sdist: stage-frontend
	uvx maturin sdist --out dist
	@echo "built:" && ls -la dist/*.tar.gz

# Both distributions need frontend/dist staged where `[tool.maturin] include` looks.
stage-frontend: build-frontend
	rm -rf python/tubulin/_frontend
	cp -R frontend/dist python/tubulin/_frontend

build-frontend:
	cd frontend && npm ci && npm run build

# Prove a built distribution runs before it can be published: clean venv outside the
# repo, real app, and the frontend's assets actually served (see scripts/smoke-dist.sh).
smoke-wheel:
	./scripts/smoke-dist.sh $(firstword $(wildcard dist/*.whl))

smoke-sdist:
	./scripts/smoke-dist.sh $(firstword $(wildcard dist/*.tar.gz))

# ------------- formatting --------------
format-rust:
	cargo fmt

format-python:
	uvx ruff format python tests examples

format-frontend:
	cd frontend && npm run format

format-all: format-rust format-python format-frontend

# ------------- linting ---------------
# `fmt --check` belongs here, not just in `format-rust`: CI's rust job runs it as a
# gate, so `make lint-all` has to fail on the same thing CI fails on.
lint-rust:
	cargo fmt --check
	cargo clippy --all-targets -- -D warnings

lint-python:
	uvx ruff check --select E9,F python tests examples

lint-frontend:
	cd frontend && npm run format:check
	cd frontend && npm run lint
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

# ------------- auditing --------------
# Dependency advisories; needs `cargo install cargo-audit` locally, CI installs it.
audit-rust:
	cargo audit

audit-frontend:
	cd frontend && npm audit --audit-level=high

audit: audit-rust audit-frontend

check: format-all lint-all
