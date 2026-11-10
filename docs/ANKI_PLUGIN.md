# Arete Anki Add-on

The **Arete Anki Add-on** (`arete_ankiconnect`) is a specialized bridge that allows the Arete CLI and Obsidian Plugin to communicate directly with your running Anki instance.

## Why is this needed?

> [!WARNING]
> This add-on **replaces** the standard AnkiConnect. **Do NOT** install both at the same time, or they will conflict on port 8765.

While Arete can sync notes directly to the Anki database (using `AnkiDirectAdapter`), many of the real-time features require Anki to be open. The add-on provides a REST API that enables:

- **Live Hover Previews**: See your card content in Obsidian without switching apps.
- **Statistical Analytics**: Fetch real-time retention and lapse data.
- **GUI Integration**: Automatically open the Anki Browser to a specific note from Obsidian.

## Installation

### Manual Installation

1.  Download the repository or the release artifact.
2.  Open Anki.
3.  Go to `Tools` -> `Add-ons`.
4.  Click `View Files`. This opens your Anki add-ons folder.
5.  Copy the `arete_ankiconnect` folder from this repository into the add-ons folder.
6.  Restart Anki.

### For Developers (Auto-Push)

To have your changes reflect immediately in Anki (upon restart) without copying files manually, use a **Symbolic Link**.

**Mac / Linux:**
Run this from the project root:
```bash
# Remove existing folder if present
rm -rf "$HOME/Library/Application Support/Anki2/addons21/arete_ankiconnect"

# Create Symlink
ln -s "$(pwd)/arete_ankiconnect" "$HOME/Library/Application Support/Anki2/addons21/arete_ankiconnect"
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType SymboliCLink -Path "$env:APPDATA\Anki2\addons21\arete_ankiconnect" -Target "$(Get-Location)\arete_ankiconnect"
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
