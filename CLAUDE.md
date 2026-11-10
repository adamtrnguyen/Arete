# CLAUDE.md — Arete

## Invariants

These hold for every change. One marked "not yet" is a bug to fix, never a design choice.
Each invariant gets a test that fails when it breaks; its status changes only with that test.

| # | Invariant | Holds today |
|---|---|---|
| 1 | **Obsidian is ground truth.** Every sync sets Anki's fields, tags, deck and note type for a card id to the vault's. An edit made in Anki is overwritten. Anki's own `leech` and `marked` tags stay. Tags are stored as Anki stores them: a tag with a space becomes two tags. | Holds. An edit made in Anki while a sync runs can survive that sync; the next one overwrites it |
| 2 | **The Arete id is the identity.** A card is found by its Anki note id, then its Arete id tag, then its first field, and is created only if none matches. The same id on two cards, or the same first field in one deck and note type, is refused. Both backends do the same. | Holds |
| 3 | **Both backends behave the same.** Whether Anki is open changes how Arete reaches the collection, never what it does. | Holds |
| 4 | **Arete never touches scheduling.** Review history, due dates and suspension belong to Anki. | Holds, a card in `Arete::Queue` included |
| 5 | **A sync writes only ids into the vault.** The Arete id, the note id and the card id. | Note bodies hold. Not yet for frontmatter: a rewritten file loses comments and formatting, and a `\|` block becomes `\|-` |
| 6 | **Nothing is deleted without confirmation.** | Holds |

The proving tests are `tests/e2e/test_sync_invariants.py` and `tests/e2e/test_sync_equivalence.py`.
A "not yet" is a strict expected failure there, so fixing it fails the suite until the mark and
this table are updated together.

## Overview

Arete is a one-way sync tool: Obsidian → Anki. Obsidian is the source of truth. It parses YAML frontmatter from markdown files, syncs cards to Anki, and builds dependency-aware study queues.

**Source:** `/Users/adam/Research/ObsidianSuite/arete`
**Vault:** `/Users/adam/Obsidian Vault`

The live vault is `~/Obsidian Vault`, which is also `arete_ankiconnect`'s `vault_root`.

## Architecture

**Domain-Driven Design** with strict layered architecture enforced by `import-linter`:

```
interface → composition → application → infrastructure → domain
```

- `src/arete/domain/` — Models, role ports (`NoteStorePort`, `SyncPort`, `QueuePort`, `CardStatsPort`, `AnkiAdminPort`; `AnkiBridge` combines them), constants
- `src/arete/infrastructure/` — Adapters (AnkiConnect HTTP, AnkiDirect file-based, stats)
- `src/arete/application/` — Use cases (sync, queue builder, graph resolver, config, stats)
- `src/arete/interface/` — CLI (Typer), the only interface
- `src/arete/composition/` — the composition root: the only package that builds adapters (`factory.py`)

Import rules:
- Domain imports nothing from other layers
- Application cannot import from `infrastructure.adapters` (uses interfaces)
- Interface reaches adapters only through `composition/`
- Verified by `just layers` (`lint-imports`)

## Anki Adapters

Two backends, auto-selected by `composition/factory.py`:

| Backend | When | How |
|---------|------|-----|
| **AnkiConnect** (`anki_connect.py`) | Anki is running | HTTP to `localhost:8765` via `arete_ankiconnect` plugin |
| **AnkiDirect** (`anki_direct.py`) | Anki is closed | Opens `collection.anki2` directly via Anki Python libs |

Both implement `AnkiBridge` interface (`domain/interfaces.py`). Neither decides what a sync
does: `application/sync/note_sync.py` (`NoteSync`) decides for both, over the batch
primitives of each adapter's `NoteStorePort` session.

### Arete AnkiConnect Plugin

Location: `~/Library/Application Support/Anki2/addons21/arete_ankiconnect/`

Fork of AnkiConnect with custom actions:
- `createFilteredDeck(name, cids, reschedule)` — Creates a filtered (dynamic) deck with cards in specified CID order
- `getFSRSStats(cards)` — Fetches FSRS difficulty scores for cards
- `getModelInfo(models)` — Per note type: kind (cloze or not), field names, template count
- `getHomeDecks(cards)` — Per card: the deck it belongs to and the filtered deck it sits in
- `setHomeDeck(cards, deck)` — Files cards under a deck; a queued card stays queued

The sync needs the last three. An add-on without one stops the sync with `AddonOutdated`,
whose message says to update the add-on and restart Anki.

Plugin must be reloaded (restart Anki) after code changes.

## Development — Justfile

**All development commands go through the justfile.** Run `just` to see available recipes.

### Core Recipes

| Recipe | What it does |
|--------|-------------|
| `just test` | Unit and e2e tests on 6 pytest-xdist workers (`ARETE_TEST_WORKERS` overrides); no Anki needed |
| `just test-integration` | Integration tests (requires Dockerized Anki) |
| `just coverage` | The same tests with a coverage report; fails under the threshold in `pyproject.toml` |
| `just lint` | Ruff linter check (no fixes) |
| `just fmt-check` | Ruff format check |
| `just fix` | Opt-in fixers: ruff fix + format, prettier on the plugin |
| `just types` | Pyright type checking (alias `check-types`) |
| `just layers` | Import-linter layer enforcement (alias `check-architecture`) |
| `just dead` | Vulture dead-code check |
| `just qa` | The gate: every check at once, every failure named, never rewrites a file (`docs/CONTRIBUTING.md`) |

### Typical Workflow

```bash
just fix              # auto-fix lint + format
just qa               # the gate; the pre-push hook runs it too
```

## Testing

| Directory | Needs | What |
|---|---|---|
| `tests/domain`, `application`, `infrastructure`, `interface` | nothing | unit tests (`just test`) |
| `tests/e2e` | nothing | whole syncs against a collection in `tmp_path` |
| `tests/integration` | Docker running (OrbStack on macOS) | a throwaway Anki container per session (`just test-integration`) |

Details (image, ports, `ANKI_CONNECT_URL`) are in `tests/integration/conftest.py`.

## CLI

Commands: `docs/CLI.md` or `arete <cmd> --help`. Agent workflows: `skills/arete`.
The Obsidian plugin and agents call the CLI and read its JSON output. `arete queue` covers
every deck unless `--deck` is set, and a named deck is an isolated set unless `--cross-deck`
is given.

### CLI Safety Rules

- **Never use `--force`**. Always let prune show what it will delete and prompt for confirmation.
- **Never use `--backend direct` or `--backend ankiconnect`**. Always use `--backend auto` (the default). Manually selecting a backend risks database corruption.

## Dependency Graph & Queue Builder

Cards declare prerequisites via `deps.requires` in YAML frontmatter. The queue builder (`application/queue/builder.py`) and graph resolver (`application/queue/graph_resolver.py`) create topologically-sorted study sessions.

**Flow:**
1. `get_due_cards()` — Find due (and optionally new) cards in Anki
2. `map_nids_to_arete_ids()` — Convert Anki note IDs to arete IDs via tags
3. `build_simple_queue()` / `build_dynamic_queue()` — Build graph from vault, walk prereq chains, topo sort
4. `get_card_ids_for_arete_ids()` — Resolve arete IDs to Anki CIDs (order-preserving)
5. `create_topo_deck()` — Create filtered deck with cards in topo order

**Filtered deck behavior:**
- Cards stay in home decks (`odid`/`odue` back up original state)
- `Arete::Queue` is a view, not a physical move
- Cards return to home decks automatically when emptied

## Obsidian Plugin (TypeScript)

Location: `obsidian-plugin/` — an Obsidian community plugin bundled with esbuild.

Style rules for plugin code and UI: [`obsidian-plugin/CLAUDE.md`](obsidian-plugin/CLAUDE.md). The plugin looks and behaves like part of Obsidian: native settings and views, sentence-case command names, no default hotkeys.

Same DDD layer structure as the Python backend: `domain/`, `infrastructure/`, `application/`, `presentation/`.

### Plugin Recipes

| Recipe | What it does |
|--------|-------------|
| `just build-obsidian` | Type-check + production build |
| `just test-obsidian` | Jest tests |
| `just coverage-obsidian` | Jest with coverage; enforces the threshold in `jest.config.js` |
| `just lint-obsidian` | ESLint |
| `just dev-plugin` | esbuild dev watcher (hot-reload) |

### Deploying a build

There is no deploy step: `just build-obsidian` writes `main.js`/`styles.css` into
`obsidian-plugin/`. Copy them and `manifest.json` into the vault's
`.obsidian/plugins/arete/`, reload the plugin, and close any Arete views opened before
the reload (they keep running the old code).

### Stack

- TypeScript 6, esbuild bundler
- Jest for tests, ESLint 10 (flat config `eslint.config.mjs`) + Prettier for lint/format
- Dependencies: CodeMirror 6 (YAML editor), D3 + 3d-force-graph (3D graph, on three.js), Mustache (templates)

## Open work

**Backend**

- [ ] `arete vault check` does not detect a duplicate Arete id; only a sync refuses one.
- [ ] Five Anki admin verbs are called only from `interface/`, with no use-case between.

**Release plumbing**

- [ ] PyPI trusted publishing is unconfigured. `release.yml:64` runs the publish step
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
- Deps references: arete ULID (one card), basename (all cards in that file), or `folder/Name`
- Agent skills that ship with arete live in `skills/` (curated list: `skills/README.md`)
- Ruff for Python linting + formatting (line-length 100)
- Pyright for Python type checking
- ESLint + Prettier for TypeScript
