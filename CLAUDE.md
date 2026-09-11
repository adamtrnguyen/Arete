# CLAUDE.md — Arete

## Overview

Arete is a one-way sync tool: Obsidian → Anki. Obsidian is the source of truth. It parses YAML frontmatter from markdown files, syncs cards to Anki, and builds dependency-aware study queues.

**Source:** `/Users/adam/Research/ObsidianSuite/arete`
**Vault:** `/Users/adam/Obsidian Vault`

> [!warning] A second vault copy exists and is stale
> `~/Library/CloudStorage/OneDrive-Personal/Obsidian Vault` is also a git repo. Its
> `.git` last moved 2026-08-29, against 2026-09-10 for the live one. The
> `arete_ankiconnect` config still names the OneDrive path as `vault_root`. Edit the
> live vault, and check that setting before you trust a sync.

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

Two backends, auto-selected by `composition/factory.py`:

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

`tests/integration` needs a running Anki. The suite manages that itself.

```bash
just test-integration
```

`tests/integration/conftest.py` starts one container for the session, on a **random
free port**, with a fresh collection under `tmp_path`. It tears the container down
after. Start OrbStack first. With Docker unreachable the suite skips rather than
falling back to a local Anki.

The image is `ghcr.io/adamtrnguyen/arete/anki-custom:latest`, built from
`docker/Dockerfile`. Nothing pulls or builds it for you: a missing image errors.

`tests/e2e` needs no container. It drives the direct backend against a real
collection in `tmp_path`.

### Pointing tests at a real Anki

Set `ANKI_CONNECT_URL`, and the conftest skips Docker entirely and uses that
instance. 🛑 Never point it at a collection you study from. `tests/conftest.py`
carries a session guard that fails the run if a real `collection.anki2` moves, but it
fires after the write, not before.

### Port convention

| Context | Port |
|---|---|
| Integration container | random, assigned per session |
| Local Anki | 8765 |

### Test Categories

| Directory | Requires Anki | What |
|-----------|--------------|------|
| `tests/domain/` | No | Domain model tests |
| `tests/application/` | No | Use case / service tests |
| `tests/infrastructure/` | No | Adapter unit tests (mocked) |
| `tests/interface/` | No | CLI + MCP server tests (mocked) |
| `tests/integration/` | **container** | Full sync and bridge tests over AnkiConnect |
| `tests/e2e/` | No | Whole-sync scenarios against a collection in `tmp_path` |

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

## Open work

No dates and no version narrative here. The history, with what was verified and when,
lives in `docs/history/`.

**Backend**

- [ ] `arete vault check` does not detect a duplicate Arete id. A run against a real
      978-note vault found three.
- [ ] Reconcile-by-Arete-id lives in the AnkiConnect adapter only. The direct backend
      creates a second copy when a vault carries a note id Anki never issued. A strict
      xfail at `tests/e2e/test_local_sync.py:119` records it and fails the day it is fixed.
- [ ] The three surfaces duplicate their wiring. `http_server` resolves config 11 times
      and builds a bridge 8 times inline. Five Anki admin verbs are called only from
      `interface/`, with no use-case between.

**Windows only** — macOS and Ubuntu pass, so these need a Windows runner.

- [ ] `test_common.py::test_to_list_path` and
      `test_models.py::TestAnkiNote::test_to_dict_converts_path` emit OS separators
      where a vault path should stay POSIX.
- [ ] `test_graph_resolver.py::...resolves_nfd_filename_with_nfc_ref` raises
      `UnicodeDecodeError` reading an NFD filename.

**Release plumbing**

- [ ] `arete_ankiconnect/manifest.json` reads 2.2.1. Everything else reads 2.4.0. A
      pre-tool hook blocks editing ankiconnect files.
- [ ] PyPI trusted publishing is unconfigured. `release.yml:53` runs the publish step
      under `continue-on-error: true`, so it cannot block a release. Configure a
      trusted publisher, or drop the step.
- [ ] Delete the dangling `v2.2.1` tag on origin: `git push origin :v2.2.1`.
- [ ] Bump the GitHub Actions versions (Dependabot PR #48).


## Key Conventions

- **Git**: one maintainer. Commit to `main` and push. No branch, no pull request.
  Run `just qa` first. Add `just test-integration` when the change touches the sync
  path or the bridge.
- **Python**: 3.12+, managed with `uv`, async throughout
- **TypeScript**: 6.x, esbuild bundler, Jest tests
- Card IDs: `arete_` prefix + 26-char ULID, auto-generated on first sync
- `anki.nid`/`anki.cid` in YAML: written by Arete after sync, never manually set
- Deps references: arete ULID (specific card) or basename (all cards in that file)
- Ruff for Python linting + formatting (line-length 100)
- Pyright for Python type checking
- ESLint + Prettier for TypeScript
