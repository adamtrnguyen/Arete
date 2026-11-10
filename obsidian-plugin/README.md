# Arete (Obsidian to Anki sync)

One-way sync from your vault to Anki. Cards live in a note's frontmatter, and the plugin runs the `arete` command-line tool to sync them. Full guide: [docs/PLUGIN.md](../docs/PLUGIN.md).

## What it does

- **Sync** cards to Anki from the command palette, the ribbon or the status bar.
- **Media and math**: image embeds are copied to Anki, and `$...$` becomes MathJax.
- **Prune**: the `Sync (prune deleted cards)` command lists Anki notes whose card left the vault and asks before deleting.
- **Tags**: frontmatter tags become Anki tags.
- **Views**: a card editor with an Anki preview, a card gutter, local and global dependency graphs, and a dashboard with review stats and a queue builder.

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

Run `Arete: Sync` from the command palette (`Cmd/Ctrl + P`). The card schema is in [skills/arete/references/SCHEMA.md](../skills/arete/references/SCHEMA.md).

## Requirements

- **Anki** desktop. The `arete_ankiconnect` add-on is needed while Anki is open.
- **Python 3.12 or later** with the `arete` command: `uv tool install git+https://github.com/adamtrnguyen/Arete`.

## Installation

1.  Download `main.js`, `manifest.json` and `styles.css` from [GitHub Releases](https://github.com/adamtrnguyen/Arete/releases/latest).
2.  Put them in `<vault>/.obsidian/plugins/arete/` and enable the plugin.
3.  In the plugin settings, set `Python executable` to the `arete` or `python3` you installed.

## License

GPL-3.0. See [LICENSE](LICENSE).
