# Arete Plugin Guide

The **Arete** Obsidian Plugin provides a rich graphical interface for synchronization, health analysis, and study queue generation.

## Installation

1.  Download the latest `main.js`, `manifest.json`, and `styles.css` from [GitHub Releases](https://github.com/adamtrnguyen/Arete/releases).
2.  Create a folder: `<Vault>/.obsidian/plugins/arete`.
3.  Place the 3 files into that folder and enable the plugin in Obsidian settings.

## Core Features

### 🔄 Intelligent Sync
- **Automatic Sync**: Enabling "Sync on Save" in settings pushes changes to Anki as you type.
- **Manual Sync**: Click the Sync icon in the ribbon or use `Mod+Shift+A`.
- **Prune Mode**: Removes cards from Anki that no longer exist in your vault.

### 🧬 Card Health & Gutter
The **Card Gutter** (enabled by default) adds visual indicators next to your notes:
- **Index Number**: Shows the card's position in the current file.
- **Health Bar**: Green, orange or red, from FSRS difficulty and lapses.
- **Difficulty or Ease**: FSRS difficulty out of 10, or SM-2 ease (`E:nn%`).
- **Lapse Counter**: The lapse count (`3L`), red above 5.

### 📐 Queue Builder
Generate custom Anki Filtered Decks that respect your knowledge graph:
1.  Open the Dashboard (**Open Dashboard** command or ribbon icon) and pick the **Queue Builder** tab.
2.  Choose a deck, a prerequisite depth and a card limit.
3.  Click **Build Queue** to see the cards in prerequisite order.
4.  Click **Send to Anki** to create the filtered deck (`Arete Study Queue`).

### 🔗 Dependency Management
Manage prerequisites directly in your notes:
- **Graphs**: The local and global graph views draw `deps.requires` edges; hovering a node previews its card.
- **Editing**: Edit `deps` in the note or in the YAML editor (**Open YAML Editor** command).

## Settings Configuration

- **Execution Mode**:
    - `CLI`: Spawns a process for each action (Classic).
    - `Server`: Connects to a persistent background server (**Recommended** for near-instant UI).
- **Python Path**: Path to your `python3` or `arete` executable.
- **Project Root**: The Arete checkout (the folder with `pyproject.toml`). Needed only when the plugin runs Arete through `uv run`.
- **Parallel Workers**: Adjust based on your CPU for faster syncs.

## Troubleshooting
- **Logs**: Check the developer console (Cmd+Option+I) or the local `run_*.log` files for sync errors.
- **Environment**: Use the "Test" button in settings to verify your Python environment is ready.
