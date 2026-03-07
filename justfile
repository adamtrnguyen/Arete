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
    {{PYTEST}} {{TESTS}}/application {{TESTS}}/interface {{TESTS}}/infrastructure {{TESTS}}/domain {{args}}

# Run backend integration tests (auto-starts Docker, random port)
test-integration *args:
    {{PYTEST}} {{TESTS}}/integration {{args}}

# Run tests with coverage
coverage:
    {{PYTEST}} --cov=src/arete --cov-report=xml --cov-report=term-missing {{TESTS}}/application {{TESTS}}/interface {{TESTS}}/infrastructure {{TESTS}}/domain

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
    mkdir -p {{RELEASE}}
    cd arete_ankiconnect && zip -r ../{{RELEASE}}/arete_ankiconnect.zip . -x "__pycache__/*"
    cp {{RELEASE}}/arete_ankiconnect.zip {{RELEASE}}/arete_ankiconnect.ankiaddon

# Full release build (all artifacts)
@release: build-python build-obsidian build-anki
    @echo "📦 Packaging release artifacts..."
    @cp dist/* {{RELEASE}}/
    @cp {{PLUGIN}}/main.js {{PLUGIN}}/manifest.json {{PLUGIN}}/styles.css {{RELEASE}}/
    @echo "✨ Release ready in {{RELEASE}}/"


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
    just test
    @echo "--- 🟦 Frontend QA ---"
    {{NPM}} run format
    just test-obsidian
    just lint-obsidian
    just build-obsidian
    @echo "✅ All QA checks passed!"

# Integration tests with full Docker lifecycle (start -> test -> stop)
test-anki *args:
    #!/usr/bin/env bash
    set -e
    cleanup() { just docker-down; }
    trap cleanup EXIT
    just docker-up
    just wait-for-anki
    {{PYTEST}} {{TESTS}}/integration {{args}}

# Integration tests with Docker lifecycle for Mac/OrbStack
mac-test-anki *args:
    #!/usr/bin/env bash
    set -e
    cleanup() { just docker-down; }
    trap cleanup EXIT
    just mac-docker-up
    just wait-for-anki
    {{PYTEST}} {{TESTS}}/integration {{args}}

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
