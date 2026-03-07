# CLAUDE.md — Arete

## Overview

Arete is a one-way sync tool: Obsidian → Anki. Obsidian is the source of truth. It parses YAML frontmatter from markdown files, syncs cards to Anki, and builds dependency-aware study queues.

**Source:** `/Users/adam/Research/arete`
**Vault:** `/Users/adam/Library/CloudStorage/OneDrive-Personal/Obsidian Vault`

## Architecture

- **Domain-Driven Design** with clean architecture layers
- `src/arete/domain/` — Models, interfaces, constants
- `src/arete/application/` — Use cases (sync, queue builder, graph resolver, config)
- `src/arete/infrastructure/` — Adapters (AnkiConnect HTTP, AnkiDirect file-based, stats)
- `src/arete/interface/` — CLI (Typer)
- `tests/` — Unit, integration, e2e tests (pytest + pytest-asyncio)

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
- `createFilteredDeck(name, cids, reschedule)` — Creates a filtered (dynamic) deck with cards in specified CID order. Uses `odue`/`odid` to preserve original card state. Sets `due = i + 1000` to enforce topological ordering.
- `getFSRSStats(cards)` — Fetches FSRS difficulty scores for cards

Plugin must be reloaded (restart Anki) after code changes.

## Dependency Graph & Queue Builder

Cards declare prerequisites via `deps.requires` in YAML frontmatter. The queue builder (`application/queue_builder.py`) and graph resolver (`application/graph_resolver.py`) create topologically-sorted study sessions.

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
- `due` values enforce presentation order (lower = shown first)

## CLI Commands

```bash
# Sync vault to Anki
uv run arete sync

# Build study queue
uv run arete anki queue --deck "Research Methodology" --include-new
uv run arete anki queue --dry-run  # preview without creating deck

# Stats and insights
uv run arete anki stats
uv run arete anki insights
```

## Development

```bash
# Run unit tests (no Anki required)
uv run pytest tests/ --ignore=tests/e2e --ignore=tests/integration -x -q

# Run specific adapter tests
uv run pytest tests/infrastructure/adapters/anki_connect/ -x -q

# Type checking
uv run mypy src/arete/
```

## Key Conventions

- Python 3.12+, managed with `uv`
- Async throughout (adapters use `async/await`)
- Card IDs: `arete_` prefix + 26-char ULID, auto-generated on first sync
- `anki.nid`/`anki.cid` in YAML: written by Arete after sync, never manually set
- Deps references: arete ULID (specific card) or basename (all cards in that file)
