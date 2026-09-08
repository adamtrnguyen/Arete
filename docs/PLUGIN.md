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
- **Retention Status**: Colored badges for "High", "Med", or "Low" retention based on Anki FSRS/SM-2 data.
- **Lapse Counter**: Identifies "Leech" cards that need attention.

### 📐 Queue Builder (v2.0)
Generate custom Anki Filtered Decks that respect your knowledge graph:
1.  Open the **Queue Builder** from the command palette.
2.  Select the concepts/notes you want to study.
3.  Click **Build Queue** to see the topologically sorted list.
4.  Click **Create Anki Deck** to generate a study-ready deck in Anki.

### 🔗 Dependency Management
Manage prerequisites directly in your notes:
- **Hover Preview**: Hover over any note ID or heading to see a preview of the card content.
- **Graph Integration**: Right-click nodes in the local graph to add/remove dependencies.

## Settings Configuration

- **Execution Mode**: 
    - `CLI`: Spawns a process for each action (Classic).
    - `Server`: Connects to a persistent background server (**Recommended** for near-instant UI).
- **Python Path**: Path to your `python3` or `arete` executable.
- **Project Root**: Path to your `obsidian_2_anki` folder (required for `uv` support).
- **Parallel Workers**: Adjust based on your CPU for faster syncs.

## Troubleshooting
- **Logs**: Check the developer console (Cmd+Option+I) or the local `run_*.log` files for sync errors.
- **Environment**: Use the "Test" button in settings to verify your Python environment is ready.
