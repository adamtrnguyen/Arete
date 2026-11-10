# Arete Plugin Guide

The **Arete** Obsidian plugin adds sync, card health, graph and study queue views on top of the `arete` CLI.

## Installation

1.  Download the latest `main.js`, `manifest.json`, and `styles.css` from [GitHub Releases](https://github.com/adamtrnguyen/Arete/releases).
2.  Create a folder: `<Vault>/.obsidian/plugins/arete`.
3.  Place the 3 files into that folder and enable the plugin in Obsidian settings.

## Core features

### Sync
- **Sync on save**: off by default. When on, a Markdown file syncs after Obsidian writes it to disk (autosave included) and the setting `Debounce delay (ms)` passes without another write.
- **Manual sync**: click the ribbon icon `Sync to Anki (Arete)`, click the status bar item, or run the `Sync` command. The plugin sets no default hotkey; bind one in Settings, Hotkeys.
- **Other commands**: `Sync current file`, `Sync (prune deleted cards)`, `Sync (force re-upload all)`.

### Card health and gutter
The card gutter marks each card in the open note:
- **Index number**: the card's position in the current file.
- **Health bar**: green, orange or red, from FSRS difficulty and lapses.
- **Difficulty or ease**: FSRS difficulty out of 10, or SM-2 ease (`E:nn%`).
- **Lapse counter**: the lapse count (`3L`), red above 5.

### Queue builder
Build a filtered deck that puts prerequisites first:
1.  Run `Open dashboard` (or click the dashboard ribbon icon) and pick the **Queue builder** tab.
2.  Choose a deck filter (default `All decks`), a prerequisite depth and a max card count.
3.  Click **Build queue** to see the cards in prerequisite order.
4.  Click **Send to Anki** to create the filtered deck `Arete Study Queue`.

### Dependencies
- **Graphs**: `Open local graph` and `Open global graph` draw `deps.requires` edges; hovering a node previews its card.
- **Editing**: edit `deps` in the note, or in the card editor (`Open YAML editor`).

## Settings

- **Execution mode**:
    - `CLI (run once)`: starts a process for each action. This is the default.
    - `Server (background)`: talks to a persistent `arete serve daemon`, faster for the UI. Setting `Server port` defaults to 8777.
- **Python executable**: path to `python3` or the `arete` executable.
- **Project root**: the Arete checkout (the folder with `pyproject.toml`). Needed only when the plugin runs Arete through `uv run`.
- **Parallel workers**: sync workers, 1 to 16.
- **Test configuration**: the `Test` button checks the Python executable.

## Troubleshooting
- **Logs**: check the developer console (Cmd+Option+I) or the `run_*.log` files that `arete logs` opens.
- **Environment**: the `Test` button in settings verifies the Python environment.
