# Arete Anki Add-on

The **Arete Anki Add-on** (`arete_ankiconnect`) is a fork of AnkiConnect requiring **Anki 26.8.1 or later**. The Arete CLI and the Obsidian plugin use it to reach a running Anki.

## Why is this needed?

> [!WARNING]
> This add-on **replaces** the standard AnkiConnect. **Do NOT** install both at the same time, or they will conflict on port 8765.

When Anki is closed, Arete reads and writes `collection.anki2` directly (`AnkiDirectAdapter`), so syncing, card previews, and FSRS analytics also work offline. While **Anki is open**, Anki holds the database lock, so Arete talks to it over HTTP via `arete_ankiconnect`, which adds Arete's sync/queue/FSRS actions and enables:

- **Live Sync, Previews & Stats While Anki Is Open**: Sync cards, fetch model templates/CSS, and read FSRS stats without closing Anki.
- **GUI Integration**: Open the Anki Browser to a specific note from Obsidian (`arete anki browse`).
- **In-Review Obsidian Shortcuts**: Open the source note (`Ctrl+Shift+O` / `Cmd+Shift+O`), copy its path (`Ctrl+Shift+C` / `Cmd+Shift+C`), or report and suspend a broken card (`Ctrl+Shift+R` / `Cmd+Shift+R`).

## Installation

### From Release (Recommended)

1.  Download `arete_ankiconnect.ankiaddon` from the [latest GitHub Release](https://github.com/adamtrnguyen/Arete/releases/latest).
2.  Open Anki (26.8.1 or later).
3.  Go to `Tools` -> `Add-ons` -> `Install from file...` and select `arete_ankiconnect.ankiaddon`.
4.  Restart Anki.

### Manual Folder Copy

1.  Open Anki, go to `Tools` -> `Add-ons`, and click `View Files` to open your `addons21` folder.
2.  Copy the `arete_ankiconnect` folder from this repository into `addons21/`.
3.  Restart Anki.

### For Developers (Auto-Push)

To have your changes reflect immediately in Anki (upon restart) without copying files manually, use a **Symbolic Link**.

**macOS:**
Run this from the project root:
```bash
# Remove existing folder if present
rm -rf "$HOME/Library/Application Support/Anki2/addons21/arete_ankiconnect"

# Create Symlink
ln -s "$(pwd)/arete_ankiconnect" "$HOME/Library/Application Support/Anki2/addons21/arete_ankiconnect"
```

**Linux:**
Run this from the project root:
```bash
# Remove existing folder if present
rm -rf "$HOME/.local/share/Anki2/addons21/arete_ankiconnect"

# Create Symlink
ln -s "$(pwd)/arete_ankiconnect" "$HOME/.local/share/Anki2/addons21/arete_ankiconnect"
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType SymbolicLink -Path "$env:APPDATA\Anki2\addons21\arete_ankiconnect" -Target "$(Get-Location)\arete_ankiconnect"
```

## Configuration

The add-on listens on `webBindAddress`:`webBindPort` (`127.0.0.1:8765` by default), set in the `config.json` file within the add-on directory.

```json
{
    "apiKey": null,
    "apiLogPath": null,
    "webBindAddress": "127.0.0.1",
    "webBindPort": 8765,
    "webCorsOriginList": ["http://localhost"],
    "ignoreOriginList": [],
    "vault_root": "/path/to/vault"
}
```

`vault_root` makes the source path the add-on copies to the clipboard absolute.

## Troubleshooting

- **Port Conflict**: Ensure no other application (like the original AnkiConnect) is using port `8765`.
- **Anki Not Running**: The add-on only works while Anki is open.
- **Firewall**: Ensure your local firewall allows connections to port `8765`.
