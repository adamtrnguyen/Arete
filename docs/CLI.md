# Arete CLI Guide

This guide covers the advanced usage, configuration, and syntax for the `arete` command-line tool.

## Installation

`arete` uses [uv](https://github.com/astral-sh/uv) for lightning-fast dependency management and isolated environments.

```bash
git clone https://github.com/adamtrnguyen/Arete
cd Arete
uv sync
```

## Commands Reference

### Root Commands

| Command | Description |
| :--- | :--- |
| `arete sync` | Standard sync from Obsidian to Anki. |
| `arete sync --prune` | Sync, then list the Arete notes whose card left the vault and ask before deleting them. |
| `arete sync --prune --yes` | The same, without asking. |
| `arete sync --force` | Re-send every card, ignoring the content cache. It does not confirm a prune. |
| `arete queue` | Build `Arete::Queue` from due cards and resolved prerequisites. |
| `arete init` | Initialize a new vault configuration. |
| `arete logs` | Open the run logs directory. |
| `arete report` | Show or clear card issues reported from Anki review sessions (`--json`, `--clear N`). |

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
| `arete graph export` | Print the resolved dependency graph as JSON. |

## Advanced Sync Options

- `--dry-run`: Preview changes without applying to Anki.
- `--clear-cache`: Force re-sync of all files.
- `--prune`: Delete Anki notes Arete created whose card is gone from the vault, and decks
  that hold only such notes. A note without an Arete id tag (`arete_` + 26-char ULID) is
  never deleted, whatever deck it is in. Empty decks are listed, not deleted. Prune runs
  only on a whole-vault sync. It shows what it will delete and asks; off a terminal it
  deletes nothing and reports the list.
- `--yes`, `-y`: Confirm the prune without the prompt.
- `--force`: Re-send every card, ignoring the content cache. It does not confirm a prune.
- `--backend`: Select backend (`auto`, `ankiconnect`, `direct`).
- `--workers`: Override parallel sync worker count.

## Queue (Dependency-Sorted Study Sessions)

Arete supports dependency-aware study queues. By tagging notes with prerequisites, you can generate filtered decks in Anki that ensure you learn concepts in dependency order.

- Queue output deck name is fixed: `Arete::Queue`.
- With `--deck`, the queue is isolated to that deck unless `--cross-deck` is passed.
- `--algo` picks the ordering: `static` (default) collects due cards plus their
  prerequisites in prerequisite order, `dynamic` orders by a ready frontier.

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
vault_root = "/path/to/vault"
anki_media_dir = "/path/to/anki/collection.media"
backend = "auto"  # 'auto' (default), 'ankiconnect', or 'direct'
prune = false
```

> [!IMPORTANT]
> **WSL Media Sync**: If you are using WSL, ensure your Anki media directory is a regular Windows path that `arete` can resolve (e.g., `/mnt/c/Users/.../collection.media`).
