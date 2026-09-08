# Arete

**One-way sync from an Obsidian vault to Anki.**

[![CI](https://github.com/adamtrnguyen/Arete/actions/workflows/ci.yml/badge.svg)](https://github.com/adamtrnguyen/Arete/actions/workflows/ci.yml)
[![Coverage](docs/coverage.svg)](https://github.com/adamtrnguyen/Arete/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/arete)](https://pypi.org/project/arete/)
[![License](https://img.shields.io/github/license/adamtrnguyen/Arete)](https://github.com/adamtrnguyen/Arete/blob/main/LICENSE)

`arete` follows one rule: **Obsidian is the source of truth**. You write and edit
your study material in the vault, and `arete` pushes it to Anki. It never writes
back the other way.

---

## 🚀 Key Features

- ⚡ **Incremental sync**: an SQLite cache skips files that did not change.
- 📐 **Topological Sort**: Build filtered study queues that respect prerequisite dependencies.
- 🧬 **FSRS Support**: Native difficulty and retention analysis for modern memory schedulers.
- 🧹 **Orphan Management**: Automatically prunes deleted cards from your Anki collection.
- 🩹 **Self-Healing**: Automatically repairs duplicate IDs or broken internal references.
- 📸 **Rich Media**: Full synchronization of images, SVGs, and other attachments.
- 💻 **Cross-platform**: runs on macOS, Linux, and Windows, including WSL.

---

## 📦 Quick Start

### 1. Install CLI
`arete` uses [uv](https://github.com/astral-sh/uv) to manage dependencies.

```bash
git clone https://github.com/adamtrnguyen/Arete
cd Arete
uv sync
# To enable Agentic features (v3 preview):
# uv sync --extra agent
```

### 2. Install Plugin
Download the latest release from the [Releases](https://github.com/adamtrnguyen/Arete/releases) page and place the files in your plugin folder:
`.obsidian/plugins/arete/`

### 3. Initialize & Sync
```bash
uv run arete init   # Interactive setup wizard
uv run arete sync   # Your first sync
```

---

## 📚 Documentation

- [**CLI Guide**](./docs/cli_guide.md): Command-line options, configuration, and syntax.
- [**Obsidian Plugin Guide**](./docs/plugin_guide.md): How to use the GUI and Gutter features.
- [**Architecture**](./docs/ARCHITECTURE.md): Technical deep-dive into the core logic.
- [**Troubleshooting**](./docs/troubleshooting.md): Common fixes for WSL and networking.

---

## 📄 License
MIT
