# Arete CLI Guide

Commands, options and configuration for the `arete` command-line tool. `arete <command> --help` lists every flag.

## Installation

`arete` uses [uv](https://github.com/astral-sh/uv) for dependencies and isolated environments.

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
| `arete report` | Show or clear card issues reported from Anki review sessions (`--json`, `--clear N`). Clearing a report unsuspends its card. |

### Vault Maintenance (`arete vault`)

| Command | Description |
| :--- | :--- |
| `arete vault check PATH` | Validate a single file for arete compatibility (`--json`). Reports an Arete id declared by two cards of the file (case-insensitive). It cannot see ids shared between files: `arete graph check` reports them, and a sync refuses them. |
| `arete vault fix PATH` | Auto-fix common format errors in a file. |
| `arete vault format [PATH]` | Normalize YAML frontmatter serialization. |

### Anki Management (`arete anki`)

| Command | Description |
| :--- | :--- |
| `arete anki stats` | Fetch card statistics for given Note IDs. |
| `arete anki browse` | Open Anki browser with a query. |
| `arete anki decks` | Print every deck name as JSON: `{"decks": [...]}`. |
| `arete anki queue-deck --ids JSON` | Fill a filtered deck with the cards of the given Arete ids, in order. Prints `{"ok", "message"}`. `--deck-name` picks the deck (default `Arete Study Queue`); `--no-reschedule` stops reviews in it from counting. |
| `arete anki suspend` | Suspend cards by CID. |
| `arete anki unsuspend` | Unsuspend cards by CID. |
| `arete anki model-css MODEL` | Get CSS styling for a model. |
| `arete anki model-templates MODEL` | Get templates for a model. |

### Other

| Command | Description |
| :--- | :--- |
| `arete config show` | View current resolved configuration. |
| `arete config open` | Open the config file in your editor. |
| `arete graph check` | Check dependency graph health: cycles, isolated cards, unresolved references, duplicate Arete ids, unparsed files. |
| `arete graph export` | Print the resolved dependency graph as JSON. |

## Advanced Sync Options

- `--dry-run`: Preview changes without applying to Anki.
- `--clear-cache`: Empty the content cache first, so every file is parsed again.
- `--prune`: Delete Anki notes Arete created whose card is gone from the vault, and decks
  that hold only such notes. A note without an Arete id tag (`arete_` + 26-char ULID) is
  never deleted, whatever deck it is in, and neither is a note whose Arete id a vault card
  still declares. Empty decks are listed, not deleted. Prune runs only on a whole-vault
  sync. It shows what it will delete and asks; off a terminal it deletes nothing and
  reports the list. A run with any error deletes nothing, even with `--yes`: it lists what
  it would delete and says why. The summary line states the prune's outcome.
- `--yes`, `-y`: Confirm the prune without the prompt.
- `--force`: Re-send every card, ignoring the content cache. It does not confirm a prune.
- `--backend`: Select backend (`auto`, `ankiconnect`, `direct`).
- `--workers`: Override parallel sync worker count.

One sync runs at a time per cache database. A sync started while another holds the lock
(`cache.db.lock` beside the cache) does nothing: it prints one line on stderr, starting
`Another Arete sync is running, so this sync did nothing.`, and exits with status 75. A dry
run takes the lock too. Exit status 1 is a sync that ran and failed.

## Queue (Dependency-Sorted Study Sessions)

Arete supports dependency-aware study queues. By tagging notes with prerequisites, you can generate filtered decks in Anki that ensure you learn concepts in dependency order.

- Queue output deck name is fixed: `Arete::Queue`.
- With `--deck`, the queue is isolated to that deck unless `--cross-deck` is passed.
- `--depth` sets how many prerequisite levels to walk (default 2). `--include-new` adds unreviewed cards.
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

# Print the preview as JSON: deck, due_count, total_with_prereqs, queue
uv run arete queue PATH --dry-run --json

# All due cards across all decks
uv run arete queue

# Use dynamic ready-frontier ordering
uv run arete queue --algo dynamic
```

## Configuration (`~/.config/arete/config.toml`)

```toml
vault_root = "/path/to/vault"
anki_media_dir = "/path/to/anki/collection.media"
backend = "auto"  # 'auto' (default), 'ankiconnect', or 'direct'
prune = false
```

The vault root names the vault in every card's source field and link, and is where images
are looked up. When `vault_root` (or `ARETE_VAULT_ROOT`) is unset, or does not hold the
path given, the vault is the nearest folder at or above the path that holds `.obsidian`.
A folder in no such vault is its own vault. A file in none is refused: set `vault_root`, or
sync the vault folder.

> [!IMPORTANT]
> **WSL media sync**: `arete` copies images into the Anki media directory, so the path must be reachable from WSL, for example `/mnt/c/Users/.../collection.media`.
