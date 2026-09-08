# Project Architecture

`arete` follows a modular **Domain-Driven Design (DDD)** approach. The code is organized into layers, separating core domain logic from application services and infrastructure adapters.

## Directory Structure

The tree below is generated from the package by `scripts/gen_architecture.py`, and
`tests/test_docs_architecture.py` fails when it drifts. Do not edit it by hand.

<!-- BEGIN GENERATED: package-tree (scripts/gen_architecture.py) -->

```text
src/arete/
│
├── interface/                 How a person or a client reaches the system: CLI, HTTP, MCP.
│   ├── _common.py                 Shared utilities for all CLI submodules
│   ├── anki_commands.py           Anki card management and debugging commands
│   ├── cli.py                     Arete CLI — root commands and subgroup registration
│   ├── http_server.py             lifespan, HealthResponse, health_check...
│   ├── mcp_server.py              Arete MCP Server
│   ├── serve_commands.py          Server commands: daemon (HTTP) and MCP (stdio)
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
│   │   ├── learning_insights_service.py Learning Insights Service
│   │   ├── metrics_calculator.py  Metrics calculator for deriving insights from raw FSRS stats
│   │   ├── service.py             FsrsStatsService
│   ├── sync/                     
│   │   ├── converter.py           Markdown to Anki HTML conversion logic
│   │   ├── id_service.py          Service for managing stable Arete IDs for cards
│   │   ├── parser.py              MarkdownParser
│   │   ├── pipeline.py            RunStats, run_pipeline
│   │   ├── vault_service.py       VaultService
│   ├── utils/                    
│   │   ├── common.py              to_list, sanitize, detect_anki_paths
│   │   ├── consts.py
│   │   ├── fs.py                  iter_markdown_files, file_md5
│   │   ├── logging.py             LogEntry, RunRecorder, setup_logging...
│   │   ├── media.py               unique_media_name, build_filename_index, transform_images_in_text...
│   │   ├── text.py                normalize_filename, parse_frontmatter, UniqueKeyLoader...
│   │   ├── yaml.py
│   ├── card_editor.py             Card editing service with maturity-based stability guards
│   ├── card_reader.py             Application service for reading card data from vault markdown files
│   ├── config.py                  AppConfig, resolve_config
│   ├── report_service.py          Service for reading and managing card issue reports
│   ├── validation.py              Vault file validation: check YAML frontmatter for arete compatibility
│   ├── wizard.py                  run_init_wizard
│
├── infrastructure/            One technology each: AnkiConnect over HTTP, the Anki library, SQLite.
│   ├── adapters/                 
│   │   ├── stats/                
│   │   │   ├── connect_stats.py   Connect Stats Repository — Infrastructure adapter using AnkiConnect HTTP API
│   │   │   ├── direct_stats.py    Direct Stats Repository — Infrastructure adapter for Anki's SQLite database
│   │   ├── anki_connect.py        AnkiConnectAdapter
│   │   ├── anki_direct.py         AnkiDirectAdapter
│   ├── anki/                     
│   │   ├── fsrs.py                FSRS memory state of an Anki card, in the domain's units
│   │   ├── repository.py          Anki Repository
│   ├── persistence/              
│   │   ├── cache.py               ContentCache
│
├── domain/                    Types and ports. True whatever the technology is.
│   ├── stats/                    
│   │   ├── models.py              Domain models for FSRS statistics
│   │   ├── ports.py               Ports (interfaces) for stats retrieval
│   ├── card_models.py             Pydantic v2 models for Arete card frontmatter validation
│   ├── constants.py               Centralized constants for the Arete application
│   ├── graph.py                   Domain types for dependency graph
│   ├── interfaces.py              Ports: what the application layer is allowed to ask of the outside world
│   ├── models.py                  AnkiDeck, AnkiNote, AnkiCardStats...
│
├── __main__.py
```

| Layer | Modules | Lines | May import |
|---|---|---|---|
| `interface` | 7 | 1749 | application, composition |
| `composition` | 2 | 161 | application, infrastructure, domain |
| `application` | 24 | 4669 | domain (ports only) |
| `infrastructure` | 7 | 2143 | domain |
| `domain` | 7 | 881 | nothing in arete |

The import rules in the last column are enforced by `just check-architecture`
(import-linter), not by convention.

<!-- END GENERATED -->

```text
obsidian-plugin/        # Obsidian GUI
├── src/                # TypeScript source files
│   ├── domain/         # Types, settings, stats models
│   ├── application/    # Frontend services (Sync, Stats, Graph, Leech, LinkChecker)
│   ├── infrastructure/ # API clients (AreteClient)
│   └── presentation/   # UI components and views (Gutter, Dashboard, Graphs)
└── styles.css          # Core UI styling
```

## CLI vs Plugin

-   **CLI**: Handles all the heavy lifting—scanning files, hashing content, communicating with AnkiConnect, and writing IDs back to Markdown.
-   **Plugin**: Provides a settings page in Obsidian and a simple "Sync" ribbon icon. When clicked, it spawns a child process to run `arete sync` and captures the output to display in a modal.

This split ensures that advanced users can automate syncs via crontab or shell scripts, while causal users get a seamless integrated experience.

## The Pipeline

The application runs in 5 distinct stages, orchestrated by `application/sync/pipeline.py`:

1.  **Scanning (`VaultService`)**:
    *   Walks the directory tree.
    *   Checks `ContentCache` (MD5 hash) to see if a file needs processing.
    *   Returns a list of compatible markdown files.

2.  **Media Indexing (`media.py`)**:
    *   Builds a global filename index of all common attachment folders (e.g., `attachments`, `assets`).
    *   Enables resolving wikilinks and markdown images without needing full paths.

3.  **Async Processing (Producer/Consumer pairing)**:
    *   **Producers (`Parsing`)**: Parse Markdown, calculate content hashes, and transform media links.
    *   **Consumers (`Syncing`)**: Multi-threaded sync to Anki (via AnkiConnect or `apy`). 
    *   Caching happens here: If a card's content hash matches the cache, it's skipped.

4.  **ID Write-back (`VaultService`)**:
    *   Writes assigned `nid` (Note ID) and `cid` (Card ID) back to the Markdown frontmatter.
    *   Ensures future runs track existing cards correctly.

5.  **Pruning**:
    *   Calculates the set difference between "All IDs in Vault" vs "All IDs in Anki".
    *   Deletes Anki notes and decks that no longer exist in Obsidian (if `--prune` is enabled).

## Key Design Decisions

### 1. Filesystem-Based Media Sync
Unlike note syncing which uses an API, **Media Sync is filesystem-based**. `arete` copies images directly from your vault into Anki's `collection.media` folder. 
*   **Implication**: The CLI must have write access to the Anki media directory.
*   **Uniqueness**: Files are hashed to avoid duplicates (e.g., `image.png` becomes `image_a1b2c3d4.png`).

### 2. Obsidian as Source of Truth
The system is designed to be **Stateless** regarding logic. The state lives in Obsidian (text) and Anki (reviews). `arete` is just the bridge. We write IDs back to Obsidian so it "owns" the link to the card.

### 3. WSL Compatibility
`anki_connect.py` contains specific logic to detect WSL environments.
*   **Problem**: WSL's `localhost` != Windows `localhost`.
*   **Solution**: It attempts to find `curl.exe` (Windows binary) accessible from Linux and uses it as a bridge to communicate with Anki on the host.

### 3. Caching
`infrastructure/persistence/cache.py` maintains a lightweight SQLite database of file hashes. This allows the tool to run in milliseconds for unchanged vaults, only processing what you've actually edited.

## Development Stack

`arete` is built with a focus on developer velocity and code quality:

-   **Package Management**: `uv` for lightning-fast dependency resolution and isolated environments.
-   **Task Automation**: `just` (via `justfile`) replaces complex Makefiles for common tasks (test, lint, docker).
-   **Code Quality**: `ruff` for linting and formatting, ensuring a 100% clean codebase with modern Python rules.
-   **Testing**: `pytest` for unit/service tests, and a **Dockerized Anki** environment for end-to-end integration tests.

## Convenience Features

-   **`debug_anki.py`**: A specialized diagnostic tool to verify connectivity between the CLI and Anki (handles WSL/Networking edge cases).
-   **Self-Healing**: Automatic recovery from "Duplicate" errors by adopting existing NIDs, making it robust against manual edits in Anki.
-   **Integrated Logs**: Use `arete --open-logs` to quickly access detailed execution logs for debugging.

## Logging & Reporting

Every execution of `arete` is fully audited:

1.  **Console Output**: Clean and high-level, with verbosity controlled by `-v`.
2.  **Debug Logs**: A comprehensive log file (`run_*.log`) is generated in `~/.config/arete/logs/` for every run, capturing full stack traces and internal transitions.
3.  **Run Reports**: `logging_utils.py` generates a human-readable Markdown report (`report_*.md`) for every sync, providing stats on files scanned, cards updated, and specific error tables.

## History

Why the code looks the way it does, with dates and the measurements that drove it:

- [2026-09-08 — the duplicate storm](history/2026-09-08-duplicate-storm.md): how
  6814 duplicate notes were created, the four faults behind it, and the tests
  that now prevent it.
