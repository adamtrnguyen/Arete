# Arete CLI Guide

This guide covers the advanced usage, configuration, and syntax for the `arete` command-line tool.

## Installation

`arete` uses [uv](https://github.com/astral-sh/uv) for lightning-fast dependency management and isolated environments.

```bash
git clone https://github.com/Adanato/Arete
cd Arete
uv sync
```

## Commands Reference

### Root Commands

| Command | Description |
| :--- | :--- |
| `arete sync` | Standard sync from Obsidian to Anki. |
| `arete sync --prune` | Sync and delete notes in Anki that were removed from Obsidian. |
| `arete sync --force` | Bypass destructive-action confirmations (typically used with `--prune`). |
| `arete queue` | Build `Arete::Queue` from due cards and resolved prerequisites. |
| `arete init` | Initialize a new vault configuration. |
| `arete logs` | Open the run logs directory. |

### Vault Maintenance (`arete vault`)

| Command | Description |
| :--- | :--- |
| `arete vault check PATH` | Validate a single file for arete compatibility. |
| `arete vault fix PATH` | Auto-fix common format errors in a file. |
| `arete vault format [PATH]` | Normalize YAML frontmatter serialization. |

### Anki Management (`arete anki`)

| Command | Description |
| :--- | :--- |
| `arete anki stats` | Fetch card statistics for given Note IDs. |
| `arete anki browse` | Open Anki browser with a query. |
| `arete anki suspend` | Suspend cards by CID. |
| `arete anki unsuspend` | Unsuspend cards by CID. |
| `arete anki model-css MODEL` | Get CSS styling for a model. |
| `arete anki model-templates MODEL` | Get templates for a model. |

### Servers (`arete serve`)

| Command | Description |
| :--- | :--- |
| `arete serve daemon` | Start the persistent HTTP server (uvicorn). |
| `arete serve mcp` | Start MCP server for AI agent integration. |

### Other

| Command | Description |
| :--- | :--- |
| `arete config show` | View current resolved configuration. |
| `arete config open` | Open the config file in your editor. |
| `arete graph check` | Check dependency graph health. |

## Advanced Sync Options

- `--dry-run`: Preview changes without applying to Anki.
- `--clear-cache`: Force re-sync of all files.
- `--prune`: Remove orphaned cards from Anki.
- `--force`: Skip confirmation prompts for destructive actions.
- `--backend`: Select backend (`auto`, `ankiconnect`, `direct`).
- `--workers`: Override parallel sync worker count.

## Queue (Dependency-Sorted Study Sessions)

Arete supports dependency-aware study queues. By tagging notes with prerequisites, you can generate filtered decks in Anki that ensure you learn concepts in dependency order.

- Queue output deck name is fixed: `Arete::Queue`.
- With `--deck`, the queue is isolated to that deck unless `--cross-deck` is passed.
- `--include-related` is reserved and currently not implemented.

```bash
# Build queue for a specific deck (due cards only)
uv run arete queue --deck "Research Methodology"

# Include new/unreviewed cards
uv run arete queue --deck "Research Methodology" --include-new

# Pull in prerequisites from other decks
uv run arete queue --deck "Research Methodology" --include-new --cross-deck

# Preview without creating deck
uv run arete queue --dry-run

# All due cards across all decks
uv run arete queue

# Use dynamic ready-frontier ordering
uv run arete queue --algo dynamic
```

## Servers

The `serve daemon` command starts a local API that the Obsidian plugin uses for near-instant interaction.

```bash
uv run arete serve daemon --port 8080
```

## Configuration (`~/.config/arete/config.toml`)

```toml
root_input = "/path/to/vault"
anki_media_dir = "/path/to/anki/collection.media"
backend = "auto"  # 'auto' (default), 'ankiconnect', or 'direct'
prune = false
```

> [!IMPORTANT]
> **WSL Media Sync**: If you are using WSL, ensure your Anki media directory is a regular Windows path that `arete` can resolve (e.g., `/mnt/c/Users/.../collection.media`).

## Deprecated Commands

The following commands still work but are hidden from `--help` and print a deprecation warning:

| Old Command | New Command |
| :--- | :--- |
| `arete check-file` | `arete vault check` |
| `arete fix-file` | `arete vault fix` |
| `arete format` | `arete vault format` |
| `arete server` | `arete serve daemon` |
| `arete mcp-server` | `arete serve mcp` |
| `arete anki queue` | `arete queue` |
| `arete anki cards-suspend` | `arete anki suspend` |
| `arete anki cards-unsuspend` | `arete anki unsuspend` |
| `arete anki models-styling` | `arete anki model-css` |
| `arete anki models-templates` | `arete anki model-templates` |
