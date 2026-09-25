# Arete

**One-way sync from an Obsidian vault to Anki.** You write cards in a note's
frontmatter; `arete` pushes them to Anki. Obsidian is the source of truth: nothing is
written back except the ids Arete assigns.

## 1. Install

```bash
uv tool install git+https://github.com/adamtrnguyen/Arete
arete init          # pick the vault and the Anki profile
```

From the [latest release](https://github.com/adamtrnguyen/Arete/releases/latest):

| File | Goes to |
|---|---|
| `main.js`, `manifest.json`, `styles.css` | `<vault>/.obsidian/plugins/arete/` (Obsidian plugin) |
| `arete_ankiconnect.ankiaddon` | Anki → Tools → Add-ons → Install from file |

## 2. Write a card

Add this to the top of any note:

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

Leave out `id:` and `anki:`. Arete writes both on the first sync.

- [Card schema](cards.md): every key, note types, deck and tag rules.
- [Writing cards](format.md): line breaks, math, cloze, dependencies.

## 3. Sync

```bash
arete sync --dry-run    # show what would change
arete sync
```

## 4. Study in prerequisite order

List what a card needs first under `deps.requires`, then:

```bash
arete queue --deck "Biology" --dry-run
arete queue --deck "Biology"      # fills the Arete::Queue filtered deck
```

## More

- `arete <command> --help` for every flag; [CLI reference](CLI.md).
- [Obsidian plugin](PLUGIN.md), [Troubleshooting](TROUBLESHOOTING.md).
- Source and issues: [GitHub](https://github.com/adamtrnguyen/Arete).
