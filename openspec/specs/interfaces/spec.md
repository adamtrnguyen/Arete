## Purpose

User-facing interfaces: CLI (Typer), MCP server (FastMCP for AI agents), and HTTP server (FastAPI for Obsidian plugin). Also covers the dual Anki backend system (AnkiConnect HTTP vs AnkiDirect SQLite) with auto-selection.

## Requirements

### Requirement: Dual Anki backend with auto-selection
The system SHALL support two Anki backends — AnkiConnect (HTTP, when Anki is running) and AnkiDirect (SQLite, when Anki is closed) — both implementing the AnkiBridge interface. Backend selection SHALL be automatic via factory.

#### Scenario: Auto-select AnkiConnect
- **WHEN** Anki is running and responding on the configured port
- **THEN** the system uses the AnkiConnect HTTP backend

#### Scenario: Auto-select AnkiDirect
- **WHEN** Anki is not running
- **THEN** the system uses the AnkiDirect backend to access the collection directly

#### Scenario: Manual override rejected
- **WHEN** the user specifies `--backend ankiconnect` or `--backend direct`
- **THEN** this is a safety violation — only `--backend auto` SHALL be used

### Requirement: CLI interface via Typer
The system SHALL expose all operations via a Typer CLI with command groups: sync, queue, vault, anki, serve, config, graph, report.

#### Scenario: CLI help
- **WHEN** the user runs `arete --help`
- **THEN** all command groups and global options are displayed

### Requirement: MCP server for AI agents
The system SHALL expose tools for sync, stats, graph analysis, queue building, card reading, and Anki browsing via a FastMCP server on stdio transport.

#### Scenario: MCP sync tool
- **WHEN** an AI agent calls `sync_vault` via MCP
- **THEN** the vault is synced to Anki and results are returned

#### Scenario: MCP card reading without Anki
- **WHEN** an AI agent calls `get_concept_cards` via MCP
- **THEN** card content is read directly from vault files without requiring Anki

### Requirement: HTTP server for Obsidian plugin
The system SHALL expose a RESTful API via FastAPI for the Obsidian plugin and other HTTP clients, including sync, card management, queue building, and Anki metadata endpoints.

#### Scenario: HTTP health check
- **WHEN** a client sends `GET /health`
- **THEN** the server responds with status, version, and uptime

#### Scenario: HTTP sync trigger
- **WHEN** a client sends `POST /sync` with config overrides
- **THEN** a sync is triggered with the provided configuration
