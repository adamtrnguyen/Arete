# Arete

**One-way sync from an Obsidian vault to Anki.**

[![CI](https://github.com/adamtrnguyen/Arete/actions/workflows/ci.yml/badge.svg)](https://github.com/adamtrnguyen/Arete/actions/workflows/ci.yml)
[![Coverage](docs/coverage.svg)](https://github.com/adamtrnguyen/Arete/actions/workflows/ci.yml)

You write cards in a note's frontmatter; `arete` pushes them to Anki. Obsidian is the
source of truth: nothing comes back except the ids Arete assigns.

**Guide:** <https://adamtrnguyen.github.io/Arete/>

## What it does

- **Sync** cards from note frontmatter to Anki. Unchanged files are skipped.
- **Study in prerequisite order:** cards list what they need under `deps.requires`, and
  `arete queue` builds a filtered deck that puts those first.
- **Prune** Anki notes whose cards were deleted from the vault, after showing you the list.
- **Media and math:** image embeds are copied to Anki; `$...$` and `$$...$$` become MathJax.
- **Obsidian plugin:** a card editor with a rendered Anki preview, dependency graphs, and
  review stats.

## Install

```bash
uv tool install git+https://github.com/adamtrnguyen/Arete
arete init
```

From the [latest release](https://github.com/adamtrnguyen/Arete/releases/latest):

| File | Goes to |
|---|---|
| `main.js`, `manifest.json`, `styles.css` | `<vault>/.obsidian/plugins/arete/` |
| `arete_ankiconnect.ankiaddon` | Anki → Tools → Add-ons → Install from file |

## A card

```yaml
---
arete: true
deck: "Biology::Anatomy"
cards:
  - Front: What are the three layers of the skin?
    Back: |-
      Epidermis, dermis, hypodermis.
  - model: Cloze
    Text: |-
      The {{c1::epidermis}} is the outermost layer of skin.
---
```

```bash
arete sync --dry-run
arete sync
```

## Docs

| | |
|---|---|
| [Card schema](skills/arete/references/SCHEMA.md) | every key, note types, decks, tags |
| [Writing cards](skills/arete/references/FORMAT.md) | line breaks, math, cloze, dependencies |
| [CLI](docs/CLI.md) | every command; `arete <command> --help` has the flags |
| [Obsidian plugin](docs/PLUGIN.md) | the card editor, graphs, stats |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | common sync and connection problems |
| [Agent skill](skills/README.md) | for coding agents that write and fix cards with you |
| [Contributing](docs/CONTRIBUTING.md) | tests, QA, architecture |

## License

GPL-3.0
