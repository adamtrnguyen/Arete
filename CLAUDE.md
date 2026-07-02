# CLAUDE.md — Arete

## Overview

Arete is a one-way sync tool: Obsidian → Anki. Obsidian is the source of truth. It parses YAML frontmatter from markdown files, syncs cards to Anki, and builds dependency-aware study queues.

**Source:** `/Users/adam/Research/ObsidianSuite/arete`
**Vault:** `/Users/adam/Library/CloudStorage/OneDrive-Personal/Obsidian Vault`

## Architecture

**Domain-Driven Design** with strict layered architecture enforced by `import-linter`:

```
interface → application → infrastructure → domain
```

- `src/arete/domain/` — Models, interfaces (`AnkiBridge` ABC), constants
- `src/arete/infrastructure/` — Adapters (AnkiConnect HTTP, AnkiDirect file-based, stats)
- `src/arete/application/` — Use cases (sync, queue builder, graph resolver, config, stats)
- `src/arete/interface/` — CLI (Typer), MCP server (FastMCP), HTTP server

Import rules:
- Domain imports nothing from other layers
- Application cannot import from `infrastructure.adapters` (uses interfaces)
- Verified by `just check-architecture` (`lint-imports`)

## Anki Adapters

Two backends, auto-selected by `application/factory.py`:

| Backend | When | How |
|---------|------|-----|
| **AnkiConnect** (`anki_connect.py`) | Anki is running | HTTP to `localhost:8765` via `arete_ankiconnect` plugin |
| **AnkiDirect** (`anki_direct.py`) | Anki is closed | Opens `collection.anki2` directly via Anki Python libs |

Both implement `AnkiBridge` interface (`domain/interfaces.py`).

### Arete AnkiConnect Plugin

Location: `~/Library/Application Support/Anki2/addons21/arete_ankiconnect/`

Fork of AnkiConnect with custom actions:
- `createFilteredDeck(name, cids, reschedule)` — Creates a filtered (dynamic) deck with cards in specified CID order
- `getFSRSStats(cards)` — Fetches FSRS difficulty scores for cards

Plugin must be reloaded (restart Anki) after code changes.

## Development — Justfile

**All development commands go through the justfile.** Run `just` to see available recipes.

### Core Recipes

| Recipe | What it does |
|--------|-------------|
| `just test` | Unit tests (no Anki required) |
| `just test-integration` | Integration tests (requires Dockerized Anki) |
| `just coverage` | Tests with coverage report (85% threshold) |
| `just lint` | Ruff linter check |
| `just format` | Ruff formatter |
| `just fix` | Auto-fix lint + format in one step |
| `just check-types` | Pyright type checking |
| `just check-architecture` | Import-linter layer enforcement |
| `just qa` | Full QA: fix → types → architecture → test → frontend |

### Typical Workflow

```bash
# After making changes:
just fix              # auto-fix lint + format
just test             # run unit tests
just check-types      # type check

# Before committing:
just qa               # full quality gate
```

## Testing with Docker (OrbStack)

Integration and e2e tests require a running Anki instance. On macOS, this runs in Docker via **OrbStack**.

### Starting Dockerized Anki

```bash
just mac-docker-up    # starts OrbStack → Docker daemon → Anki container
just wait-for-anki    # polls until AnkiConnect responds (up to 30s)
```

This starts a headless Anki 24.11 container with AnkiConnect exposed on **port 8766** (mapped from container's 8765).

### Running Integration Tests

```bash
just mac-docker-up
just wait-for-anki
just test-integration
```

### Stopping

```bash
just docker-down
```

### Port Convention

| Context | Port | URL |
|---------|------|-----|
| Docker (integration tests) | 8766 | `http://127.0.0.1:8766` |
| Local Anki (production) | 8765 | `http://127.0.0.1:8765` |

Override with `ANKI_CONNECT_URL` env var.

### Test Categories

| Directory | Requires Anki | What |
|-----------|--------------|------|
| `tests/domain/` | No | Domain model tests |
| `tests/application/` | No | Use case / service tests |
| `tests/infrastructure/` | No | Adapter unit tests (mocked) |
| `tests/interface/` | No | CLI + MCP server tests (mocked) |
| `tests/integration/` | **Yes** | Full sync/bridge tests against real Anki |
| `tests/e2e/` | **Yes** | End-to-end scenario tests |

## CLI Commands

```bash
# Sync vault to Anki
uv run arete sync

# Build study queue
uv run arete queue --deck "Research Methodology" --include-new
uv run arete queue --dry-run

# Vault maintenance
uv run arete vault check somefile.md
uv run arete vault fix somefile.md
uv run arete vault format

# Anki management
uv run arete anki stats --nids 123
uv run arete anki browse --nid 123

# Servers
uv run arete serve daemon --port 8777
uv run arete serve mcp
```

### CLI Safety Rules

- **Never use `--force`**. Always let prune show what it will delete and prompt for confirmation.
- **Never use `--backend direct` or `--backend ankiconnect`**. Always use `--backend auto` (the default). Manually selecting a backend risks database corruption.

## MCP Server

FastMCP-based server exposing Arete tools to AI agents (Claude, Gemini, etc.).

**Entry point:** `uv run arete serve mcp` (stdio transport)

### Available Tools

| Tool | What it does | Needs Anki |
|------|-------------|-----------|
| `sync_vault` | Sync vault to Anki | Yes |
| `sync_file` | Sync a single file | Yes |
| `get_stats` | Learning statistics + leeches | Yes |
| `browse_concept` | Open Anki browser for a concept | Yes |
| `browse_card` | Open Anki browser for a specific card | Yes |
| `get_concept_cards` | Read card content from vault markdown | No |
| `get_due_cards` | Show due cards with Arete IDs | Yes |
| `build_study_queue` | Build dependency-ordered filtered deck | Yes |

### MCP Config (for Claude Code)

```json
{
  "mcpServers": {
    "arete": {
      "command": "uv",
      "args": ["run", "--project", "/Users/adam/Research/ObsidianSuite/arete", "arete", "serve", "mcp"]
    }
  }
}
```

## Dependency Graph & Queue Builder

Cards declare prerequisites via `deps.requires` in YAML frontmatter. The queue builder (`application/queue/builder.py`) and graph resolver (`application/queue/graph_resolver.py`) create topologically-sorted study sessions.

**Flow:**
1. `get_due_cards()` — Find due (and optionally new) cards in Anki
2. `map_nids_to_arete_ids()` — Convert Anki note IDs to arete IDs via tags
3. `build_dependency_queue()` — Build graph from vault, walk prereq chains, topo sort
4. `get_card_ids_for_arete_ids()` — Resolve arete IDs to Anki CIDs (order-preserving)
5. `create_topo_deck()` — Create filtered deck with cards in topo order

**Filtered deck behavior:**
- Cards stay in home decks (`odid`/`odue` back up original state)
- `Arete::Queue` is a view, not a physical move
- Cards return to home decks automatically when emptied

## Obsidian Plugin (TypeScript)

Location: `obsidian-plugin/` — an Obsidian community plugin bundled with esbuild.

Same DDD layer structure as the Python backend: `domain/`, `infrastructure/`, `application/`, `presentation/`.

### Plugin Recipes

| Recipe | What it does |
|--------|-------------|
| `just build-obsidian` | Type-check + production build |
| `just test-obsidian` | Jest tests |
| `just lint-obsidian` | ESLint |
| `just dev-plugin` | esbuild dev watcher (hot-reload) |

### Stack

- TypeScript 6, esbuild bundler
- Jest for tests, ESLint 10 (flat config `eslint.config.mjs`) + Prettier for lint/format
- Dependencies: CodeMirror 6 (YAML editor), D3 + three.js (3D force graph), Mustache (templates)

## TODO

- [ ] ~~Explore Claude Agent SDK (`claude-agent-sdk`) for programmatic card quality reviews~~ — **deprioritized**: AI features were removed to keep sync simple and deterministic (the `agent` optional-dependency extra no longer exists).

### Known issues & follow-ups (surfaced by the 2.3.0 deps refresh — 2026-06-29)

All **pre-existing**: these shipped in 2.1.0 too but were hidden because CI died at
the install step (`uv sync --extra agent`, an extra that no longer exists) before
reaching them. Fixing the pipeline (`--extra agent` → `--dev`) made them visible.
**None of these block the 2.3.0 release**, which is published and is the BRAT `latest`.

**Likely real bugs — investigate first:**

- [ ] **Plugin Jest failures** (`obsidian-plugin/tests/application/services/StatsService.test.ts`, `CardParserService.test.ts` — 7 tests). Fail on pristine `main`. Determine stale-test vs. a real bug in the shipped plugin's stats-aggregation / card-parsing logic.
- [ ] **Windows path & encoding bugs** (3 Python tests, Windows-only; macOS + Ubuntu pass):
  - `test_common.py::test_to_list_path` and `test_models.py::TestAnkiNote::test_to_dict_converts_path` — emit OS separators (`\vault\note.md`) instead of POSIX (`/vault/note.md`); vault paths should be POSIX for cross-platform portability.
  - `test_graph_resolver.py::...resolves_nfd_filename_with_nfc_ref` — `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9` reading an accented (NFD) filename on Windows. Likely a real bug for Windows users with accented filenames.
- [ ] **anki 25 sync round-trip.** The 24.4.1 → 25.9.2 bump was validated by static API audit + unit tests (which mock `AnkiBridge`) but **not** a full Obsidian→Anki round-trip — integration tests are currently dark (below). FSRS-6 changed scheduling internals; confirm a real round-trip once integration can run.

**Test / CI infrastructure:**

- [ ] **Integration + e2e suites can't run anywhere.** `docker/docker-compose*.yml` pin `image: ghcr.io/adanato/arete/anki-custom:latest`, which was never published (`manifest unknown` in CI; base image also has no arm64 manifest locally). Either publish the image built from `docker/Dockerfile` to GHCR, or add a `build:` stanza so compose builds it locally. Until fixed, `tests/integration` + `tests/e2e` provide zero coverage.
- [ ] **Ruff lint gate can never pass.** `[tool.ruff.lint]` selects mutually-exclusive docstring rules (`D203`+`D211`, `D212`+`D213`) — pick one of each pair. Plus ~22 pre-existing `C901` complexity violations (max-complexity 10) in `builder.py`, `cli.py`, `pipeline.py`, `graph_resolver.py`, etc. — refactor or ignore. Also `target-version = "py311"` should be `"py312"` to match `requires-python`.
- [ ] **PyPI trusted publishing is not configured.** `release.yml`'s publish step fails `invalid-publisher`; it's deliberately `continue-on-error` so it can't block the GitHub/BRAT release. Configure a PyPI trusted publisher for `adamtrnguyen/Arete` + `release.yml`, or drop the PyPI step (PyPI has been stale since 2.0.1; the plugin ships via the GitHub release, not pip).

**Housekeeping:**

- [ ] Delete the dangling `v2.2.1` tag on origin (a tag was pushed but its release run failed, so no release exists): `git push origin :v2.2.1`.
- [ ] Bump GitHub Actions versions (Dependabot PR #48). Node 20 actions are being force-run on Node 24: `actions/checkout@v4`→v6, `astral-sh/setup-uv@v5`→v7, `actions/setup-node@v4`→v6, etc.
- [ ] `arete_ankiconnect/manifest.json` stayed at 2.2.1 (a pre-tool hook blocks editing ankiconnect files) — align its version if a consistent bump is wanted.

## Key Conventions

- **Python**: 3.12+, managed with `uv`, async throughout
- **TypeScript**: 6.x, esbuild bundler, Jest tests
- Card IDs: `arete_` prefix + 26-char ULID, auto-generated on first sync
- `anki.nid`/`anki.cid` in YAML: written by Arete after sync, never manually set
- Deps references: arete ULID (specific card) or basename (all cards in that file)
- Ruff for Python linting + formatting (line-length 100)
- Pyright for Python type checking
- ESLint + Prettier for TypeScript
