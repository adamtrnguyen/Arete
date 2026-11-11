# Project Architecture

`arete` follows a modular **Domain-Driven Design (DDD)** approach. The code is organized into layers, separating core domain logic from application services and infrastructure adapters.

## Directory Structure

The tree below is generated from the package by `scripts/gen_architecture.py`, and
`tests/test_docs_architecture.py` fails when it drifts. Do not edit it by hand.

<!-- BEGIN GENERATED: package-tree (scripts/gen_architecture.py) -->

```text
src/arete/
│
├── interface/                 How a person or a client reaches the system: CLI, HTTP.
│   ├── _common.py                 Shared utilities for all CLI submodules
│   ├── anki_commands.py           Anki card management and debugging commands
│   ├── cli.py                     Arete CLI — root commands and subgroup registration
│   ├── vault_commands.py          Vault maintenance commands: validate, fix, format
│
├── composition/               The only place that picks an adapter for a port and wires a use-case.
│   ├── factory.py                 Composition Root
│   ├── orchestrator.py            Sync orchestration — wires up services and runs the pipeline
│
├── application/               Use-cases. Decides what happens next, over ports, never over adapters.
│   ├── queue/                    
│   │   ├── builder.py             Queue builder for dependency-aware study sessions
│   │   ├── graph_resolver.py      Graph resolver for building dependency graphs from vault files
│   │   ├── service.py             Queue orchestration service
│   ├── stats/                    
│   │   ├── metrics_calculator.py  Metrics calculator for deriving insights from raw FSRS stats
│   │   ├── service.py             FsrsStatsService
│   ├── sync/                     
│   │   ├── converter.py           Markdown to Anki HTML conversion logic
│   │   ├── id_service.py          Service for managing stable Arete IDs for cards
│   │   ├── id_writeback.py        Write card ids into a note's frontmatter as line edits; every other byte stays
│   │   ├── note_sync.py           Bring Anki's notes to what the vault says: the one copy of the sync decision logic
│   │   ├── parser.py              MarkdownParser
│   │   ├── pipeline.py            vault_claims, vault_models, true_source_lines...
│   │   ├── prune.py               Prune: delete the Anki notes and decks Arete made that the vault no longer claims
│   │   ├── vault_service.py       VaultService
│   ├── utils/                    
│   │   ├── common.py              to_list, sanitize, detect_anki_paths
│   │   ├── consts.py
│   │   ├── fs.py                  replace_file, replace_text, iter_markdown_files...
│   │   ├── logging.py             LogEntry, RunRecorder, setup_logging...
│   │   ├── media.py               build_filename_index, FilenameIndex, transform_images_in_text...
│   │   ├── text.py                normalize_filename, parse_frontmatter, UniqueKeyLoader...
│   │   ├── yaml.py
│   ├── config.py                  VaultRootUnknown, find_vault_root, require_vault_root...
│   ├── report_service.py          Service for reading and managing card issue reports
│   ├── validation.py              Vault file validation: check YAML frontmatter for arete compatibility
│   ├── wizard.py                  run_init_wizard
│
├── infrastructure/            One technology each: AnkiConnect over HTTP, the Anki library, SQLite.
│   ├── adapters/                 
│   │   ├── stats/                
│   │   │   ├── connect_stats.py   Connect Stats Repository — Infrastructure adapter using AnkiConnect HTTP API
│   │   │   ├── deck_config.py     FSRS parameters from a deck config as Anki stores it
│   │   │   ├── direct_stats.py    Direct Stats Repository — Infrastructure adapter for Anki's SQLite database
│   │   ├── anki_connect.py        ConnectNoteSession, AnkiConnectAdapter
│   │   ├── anki_direct.py         AnkiDirectAdapter
│   ├── anki/                     
│   │   ├── fsrs.py                FSRS memory state of an Anki card, in the domain's units
│   │   ├── note_session.py        The note-store primitives over an open collection, for the direct backend
│   │   ├── repository.py          Anki Repository
│   │   ├── search.py              Anki search terms built from a name, so the name matches only itself
│   ├── persistence/              
│   │   ├── cache.py               cache_path, ContentCache
│   │   ├── sync_lock.py           One sync at a time per cache: an exclusive, non-blocking lock on a file beside the cache
│
├── domain/                    Types and ports. True whatever the technology is.
│   ├── stats/                    
│   │   ├── models.py              Domain models for FSRS statistics
│   │   ├── ports.py               Ports (interfaces) for stats retrieval
│   ├── constants.py               Centralized constants for the Arete application
│   ├── graph.py                   Domain types for dependency graph
│   ├── identity.py                How a vault card and an Anki note are recognised as the same card
│   ├── interfaces.py              Ports: what the application layer is allowed to ask of the outside world
│   ├── models.py                  AnkiDeck, AnkiNote, WorkItem...
│   ├── note_types.py              Field mapping for changing a note's type in place
│   ├── ownership.py               Which Anki notes Arete owns
│
├── __main__.py
```

| Layer | Modules | Lines | May import |
|---|---|---|---|
| `interface` | 4 | 1067 | application, composition |
| `composition` | 2 | 267 | application, infrastructure, domain |
| `application` | 24 | 6020 | domain (ports only) |
| `infrastructure` | 11 | 2195 | domain |
| `domain` | 9 | 875 | nothing in arete |

The import rules in the last column are enforced by `just check-architecture`
(import-linter), not by convention.

<!-- END GENERATED -->

```text
obsidian-plugin/        # Obsidian GUI
├── src/                # TypeScript source files
│   ├── domain/         # Types, settings, stats models
│   ├── application/    # Frontend services (Stats, Graph, Leech, LinkChecker, card rendering)
│   ├── infrastructure/ # Python process and API clients (AreteClient, SyncService, ServerManager)
│   └── presentation/   # UI components and views (Gutter, Dashboard, Graphs)
└── styles.css          # Built from src/styles/*.css
```

## CLI vs Plugin

-   **CLI**: does the work: scanning files, hashing content, talking to Anki (AnkiConnect, or the collection directly), and writing ids back to Markdown.
-   **Plugin**: settings, commands, the card gutter, the dashboard and the graph views. Every action runs an `arete` command as a child process and reads its output, JSON where the command prints it.

This split lets you automate syncs with crontab or shell scripts, while plugin users get an integrated experience.

## The pipeline

`application/sync/pipeline.py` runs these stages:

1.  **Scan (`VaultService`)**:
    *   Walks the directory tree.
    *   Skips parsing a file whose mtime and size match the stat cache (`ContentCache`).
    *   Returns the compatible markdown files.

2.  **Ids**:
    *   A card without an Arete id gets one, and the file is rewritten (not on a dry run).
    *   An `anki.nid` claimed by cards in several files, or an Arete id declared by several cards, is an error. Those cards are not synced.

3.  **Media indexing (`media.py`)**:
    *   Builds a filename index of the vault's attachment folders on the first image lookup.
    *   Resolves wikilinks and markdown images without full paths.

4.  **Producers and consumers**:
    *   **Producers (parse)**: parse Markdown, render it to HTML, hash the content, and copy media.
    *   **Consumers (`NoteSync`)**: batches go to Anki through the backend's note store. `NoteSync` finds each card's note (note id, Arete id tag, first field), creates what is missing, and sets fields, tags, deck and note type to the vault's.
    *   A card whose hash matches the cache is labelled unchanged. It is compared with Anki all the same.

5.  **Write-back (`VaultService`)**:
    *   Writes `nid` and `cid` into the card's `anki:` block in the frontmatter.
    *   A file counts as synced in the stat cache only when every card in it reached Anki.

6.  **Prune** (only with `--prune`):
    *   Only notes carrying an Arete id tag can be orphans (`domain/ownership.py`); a deck goes only if every note in it is one (`application/sync/prune.py`).
    *   A note the vault claims by its note id or by an Arete id a card declares is never an orphan.
    *   Deletes nothing without a confirmation: `--yes`, `confirm_prune`, or "yes" at a terminal prompt.
    *   Deletes nothing in a run with errors, confirmed or not: it lists what it would delete and says why.

## Key design decisions

### 1. Filesystem-based media sync
Notes go through the Anki backend, but **media is copied on the filesystem**. `arete` copies images from your vault into Anki's `collection.media` folder.
*   **Implication**: the CLI needs write access to the Anki media directory.
*   **Names**: a file keeps its name. If a different file of that name is already there, the new one gets an 8-character content hash suffix (`image.png` becomes `image_a1b2c3d4.png`). An identical file is not copied again.

### 2. Obsidian as source of truth
The logic holds no state of its own. The state lives in Obsidian (text) and Anki (reviews). `arete` is the bridge. Ids are written back to Obsidian so it holds the link to the card.

### 3. WSL compatibility
`anki_connect.py` detects WSL.
*   **Problem**: WSL's `localhost` is not Windows' `localhost`.
*   **Solution**: it uses `curl.exe` (a Windows binary reachable from Linux) as a bridge to Anki on the host. Without `curl.exe` it rewrites the URL with the host address from `/etc/resolv.conf`.

### 4. Caching
`infrastructure/persistence/cache.py` keeps a SQLite database of file stats, card hashes and cached renders. Unchanged files skip parsing and rendering, so a run over an unchanged vault is fast. Every card is still compared with Anki.

## Development stack

-   **Package management**: `uv`.
-   **Task automation**: `just` (see the `justfile`).
-   **Code quality**: `ruff` for linting and formatting, `pyright` for types, `import-linter` for layers.
-   **Testing**: `pytest` for unit and e2e tests, and a Dockerized Anki for integration tests.

## Logging

1.  **Console output**: high level, with verbosity set by `-v`.
2.  **Debug logs**: a `run_*.log` file per run in `~/.config/arete/logs/` (the latest 50 are kept). `arete logs` opens the directory.
