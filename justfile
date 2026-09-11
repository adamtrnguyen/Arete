# Arete Project Automation
set shell := ["sh", "-cu"]

# --- Project Constants ---
PY         := "uv run python"
PYTEST     := "uv run pytest"
RUFF       := "uv run ruff"
NPM        := "npm --prefix obsidian-plugin"
SRC        := "src"
TESTS      := "tests"
PLUGIN     := "obsidian-plugin"
RELEASE    := "release_artifacts"

# Every suite that needs no container. `test` and `coverage` both read this, so the
# two cannot drift apart. They did: `coverage` was missing test_factories.py.
UNIT       := TESTS/"application " + TESTS/"interface " + TESTS/"infrastructure " + TESTS/"domain " + TESTS/"e2e " + TESTS/"test_docs_architecture.py " + TESTS/"test_factories.py"

# Default: List all available tasks
default:
    @just --list

# --- Setup ---

# Install dependencies for both Python (uv) and Obsidian (npm)
@install:
    uv sync
    {{NPM}} install
    uv run pre-commit install

# --- Development ---

# Start backend dev server with hot-reload
@dev-backend:
    uv run uvicorn arete.interface.main:app --reload

# Start plugin dev watcher
@dev-plugin:
    {{NPM}} run dev

# --- Backend (Python) ---

# Run backend tests
test *args:
    {{PYTEST}} {{UNIT}} {{args}}

# Run backend integration tests (auto-starts Docker, random port)
test-integration *args:
    {{PYTEST}} {{TESTS}}/integration {{args}}

# Run tests with coverage
coverage:
    {{PYTEST}} --cov=src/arete --cov-report=xml --cov-report=term-missing {{UNIT}}

# Integration tests with coverage
test-integration-coverage *args:
    {{PYTEST}} --cov=src/arete --cov-report=term-missing {{TESTS}}/integration {{args}}

# All tests with coverage (unit + integration + e2e)
coverage-all *args:
    {{PYTEST}} --cov=src/arete --cov-report=term-missing {{TESTS}} {{args}}


# Lint backend code with Ruff
@lint:
    {{RUFF}} check {{SRC}} {{TESTS}} scripts/

# Format backend code with Ruff
@format:
    {{RUFF}} format {{SRC}} {{TESTS}} scripts/

# Fix all auto-fixable backend issues
@fix:
    {{RUFF}} check --fix {{SRC}} {{TESTS}} scripts/
    {{RUFF}} format {{SRC}} {{TESTS}} scripts/

# Static type checking
@check-types:
    uv run pyright {{SRC}}

# Check architectural layers and isolation
@check-architecture:
    uv run lint-imports

# Check that generated docs match the code
@check-docs:
    uv run python scripts/gen_architecture.py --check

# Check every manifest carries the version in pyproject.toml
@check-versions:
    uv run python scripts/sync_versions.py --check

# Write the pyproject version into every manifest (plugin, Anki add-on, package.json)
@sync-versions:
    uv run python scripts/sync_versions.py

# --- Frontend (Obsidian Plugin) ---

# Build Obsidian plugin
@build-obsidian:
    {{NPM}} run build

# Lint Obsidian plugin
@lint-obsidian:
    {{NPM}} run lint

# Test Obsidian plugin
@test-obsidian:
    {{NPM}} run test

# --- Release & Artifacts ---

# Build Python package (sdist + wheel)
@build-python:
    {{PY}} -m build

# Zip Anki plugin for distribution
@build-anki:
    rm -rf {{RELEASE}}
    mkdir -p {{RELEASE}}
    cd arete_ankiconnect && zip -r ../{{RELEASE}}/arete_ankiconnect.zip . -x "__pycache__/*"
    cp {{RELEASE}}/arete_ankiconnect.zip {{RELEASE}}/arete_ankiconnect.ankiaddon

# Everything a release must satisfy, without changing a single file.
# `just qa` runs `just fix`, which rewrites source; a release build must not.
@verify:
    just lint
    just check-types
    just check-architecture
    just check-docs
    just check-versions
    just test
    just test-obsidian

# Run the built wheel in a throwaway environment and call its console script.
# The Obsidian plugin shells out to `arete`, so a wheel that builds but does not
# expose that entry point is a broken release every other check would pass.
@verify-wheel:
    #!/usr/bin/env bash
    set -euo pipefail
    WHEEL=$(ls -t dist/*.whl | head -1)
    uv run --no-project --with "$WHEEL" arete --help > /dev/null
    echo "  wheel ok: $(basename "$WHEEL") exposes a working arete command"

# Full release build: verify, stamp every manifest, build all three artifacts, run the wheel
@release: verify sync-versions build-python build-obsidian build-anki verify-wheel
    @echo "📦 Packaging release artifacts..."
    @cp dist/* {{RELEASE}}/
    @cp {{PLUGIN}}/main.js {{PLUGIN}}/manifest.json {{PLUGIN}}/styles.css {{RELEASE}}/
    @echo "✨ Release ready in {{RELEASE}}/"
    @ls -1 {{RELEASE}}/


# --- QA & CI ---

# Verify V2 migration logic against mock vault
@test-migration:
    {{PY}} -m arete migrate {{TESTS}}/mock_vault -v

# Run full project QA (Tests + Linting + Formatting)
@qa:
    @echo "--- 🐍 Backend QA ---"
    just fix
    just check-types
    just check-architecture
    just check-docs
    just test
    @echo "--- 🟦 Frontend QA ---"
    {{NPM}} run format
    just test-obsidian
    just lint-obsidian
    just build-obsidian
    @echo "✅ All QA checks passed!"

# `test-anki` and `mac-test-anki` used to sit here. Both started a compose container
# on a fixed port, then ran pytest. Neither set ANKI_CONNECT_URL, so the integration
# conftest ignored that container and started a second one on a random port. Two
# containers, one wasted.
#
# Locally, run `just test-integration` on its own. The conftest manages its own
# container and tears it down after.
#
# CI still uses docker-up / wait-for-anki / docker-down below, and that path is
# correct because ci.yml exports ANKI_CONNECT_URL first.

# --- System ---

# Clean up build artifacts and caches
@clean:
    @echo "🧹 Cleaning project..."
    rm -rf dist/ {{RELEASE}}/
    find . -type d -name "__pycache__" -exec rm -rf {} +
    rm -rf .pytest_cache/ .ruff_cache/ .mypy_cache/
    @echo "✨ Cleaned."

# --- Infrastructure ---

# Download and configure AnkiConnect for Docker
@setup-anki-data:
    {{PY}} scripts/install_ankiconnect.py

# Start Dockerized Anki
@docker-up: setup-anki-data
    docker compose -f docker/docker-compose.yml up -d

# Stop Dockerized Anki
@docker-down:
    docker compose -f docker/docker-compose.yml down

# Wait for Anki to be ready
@wait-for-anki:
    {{PY}} scripts/wait_for_anki.py

# Start Dockerized Anki (optimized for Mac/OrbStack)
@mac-docker-up:
    @echo "🚀 Starting OrbStack..."
    @orb start
    @echo "⌛ Waiting for Docker daemon..."
    @while ! docker info > /dev/null 2>&1; do sleep 1; done
    @just docker-up
