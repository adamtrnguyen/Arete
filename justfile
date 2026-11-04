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
# two cannot drift apart.
UNIT       := TESTS/"application " + TESTS/"interface " + TESTS/"infrastructure " + TESTS/"domain " + TESTS/"e2e " + TESTS/"test_docs_architecture.py " + TESTS/"test_factories.py " + TESTS/"test_source_prose.py"

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

# Start the HTTP daemon with hot-reload
@dev-backend:
    uv run arete serve daemon --reload

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


# Lint backend code with Ruff (no fixes)
lint:
    {{RUFF}} check --no-fix {{SRC}} {{TESTS}} scripts/

# The tree stays formatted (fix with `just format`)
fmt-check:
    {{RUFF}} format --check {{SRC}} {{TESTS}} scripts/

# Format backend code with Ruff
@format:
    {{RUFF}} format {{SRC}} {{TESTS}} scripts/

# Fixers, opt-in and never part of qa: ruff fixes + format, prettier on the plugin
@fix:
    {{RUFF}} check --fix {{SRC}} {{TESTS}} scripts/
    {{RUFF}} format {{SRC}} {{TESTS}} scripts/
    {{NPM}} run format

# Static type checking
types:
    uv run pyright {{SRC}}

alias check-types := types

# Check architectural layers and isolation
layers:
    uv run lint-imports

alias check-architecture := layers

# Dead code: vulture, configured in [tool.vulture]
dead:
    uv run vulture

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
lint-obsidian:
    {{NPM}} run lint

# Type-check Obsidian plugin
types-obsidian:
    cd {{PLUGIN}} && npx tsc --noEmit

# Test Obsidian plugin
test-obsidian:
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

# Everything a release must satisfy, without changing a single file: the qa gate
verify: qa

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

# The gate: every check at once, every failure named. Each PID is waited on by itself
# (a bare `wait` returns only the last job's status). `failed` is a string because macOS
# bash 3.2 errors on an empty array under `set -u`. It never rewrites files: see `fix`.
# Integration tests (Dockerized Anki) stay out: `just test-integration`.
[doc("the gate: every check at once, every failure named")]
qa:
    #!/usr/bin/env bash
    set -uo pipefail
    names=(lint fmt-check types layers dead test check-docs check-versions lint-obsidian types-obsidian test-obsidian)
    logs=$(mktemp -d "${TMPDIR:-/tmp}/qa.XXXXXX")
    trap 'rm -rf "$logs"' EXIT
    echo "== qa: ${#names[@]} checks, concurrent (${names[*]}); output replayed in that order =="
    pids=()
    for name in "${names[@]}"; do
        just "$name" >"$logs/$name.out" 2>&1 &
        pids+=("$!")
    done
    failed=""
    for i in "${!pids[@]}"; do
        wait "${pids[$i]}" || failed="$failed ${names[$i]}"
    done
    for name in "${names[@]}"; do echo "== $name =="; cat "$logs/$name.out"; echo ""; done
    if [ -n "$failed" ]; then
        echo "QA FAILED:$failed"
        exit 1
    fi
    echo "QA passed: ${names[*]}."

# Locally, run `just test-integration` on its own: the integration conftest starts its
# own container and tears it down after. docker-up / wait-for-anki / docker-down below
# are for CI, which exports ANKI_CONNECT_URL so the conftest uses that container.

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
