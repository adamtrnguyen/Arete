# Arete (Obsidian to Anki Sync)

**A professional-grade, one-way sync tool from Obsidian to Anki.**

Arete treats your Obsidian vault as the **Source of Truth**. Write your flashcards in Markdown, and let Arete handle the synchronization to Anki, including media, math, and updates.

![Demo Screenshot](https://raw.githubusercontent.com/adamtrnguyen/Arete/main/docs/assets/demo.png)

## Features

- **⚡ Zero-Config Sync**: Automatically finds flashcards in your notes.
- **🖼️ Media Support**: Syncs images, audio, and video to Anki.
- **🧮 MathJax Ready**: Full support for LaTeX/MathJax equations.
- **🧹 Prune Mode**: Deleted a note in Obsidian? It's removed from Anki automatically.
- **🏷️ Tag Sync**: Obsidian tags become Anki tags.

## Usage

### 1. Basic Flashcard

Create a new note (or add to an existing one) with the `Front: Back` syntax or the default Question/Answer block.

```markdown
---
deck: Computer Science
---

# My Notes

## Question

What is the time complexity of binary search?

## Answer

O(log n)
```

### 2. Cloze Deletion

Use standard Anki cloze syntax:

```markdown
The capital of {{c1::France}} is {{c2::Paris}}.
```

### 3. Syncing

1.  Open the Command Palette (`Cmd/Ctrl + P`).
2.  Type `Arete: Sync`.
3.  Watch the notification for progress!

## Requirements

- **Anki** (Desktop application)
- **AnkiConnect** (Anki Add-on) or the **Arete Unified Add-on** (Recommended)
- **Python 3.11+** (for the backend CLI)

## Installation

1.  Install **Arete** from the Obsidian Community Plugins list.
2.  Ensure you have the backend CLI installed: `pip install arete`
3.  Configure your Vault path in the plugin settings.

## License

MIT License. See [LICENSE](https://github.com/adamtrnguyen/Arete/blob/main/LICENSE) for details.
