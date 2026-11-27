# Arete AnkiConnect Configuration

Arete AnkiConnect is an AnkiConnect-compatible add-on with custom Arete sync, queue, FSRS, and Obsidian integration actions.

## Settings

- `apiKey`: Optional API key required on requests (`null` disables authentication). Can also be set via `ANKICONNECT_API_KEY`.
- `apiLogPath`: Optional file path to log requests and replies (`null` disables logging).
- `webBindAddress`: Bind address for the HTTP server (default `"127.0.0.1"`). Can also be overridden via `ANKICONNECT_BIND_ADDRESS`.
- `webBindPort`: Listening port for the HTTP server (default `8765`). Can also be overridden via `ANKICONNECT_BIND_PORT`.
- `webCorsOriginList`: Allowed browser `Origin` headers (default `["http://localhost"]`).
- `ignoreOriginList`: Browser origins whose `requestPermission` prompts are silently denied.
- `vault_root`: Absolute path to your Obsidian vault (default `""`). Used by **Copy Obsidian Source** (`Ctrl+Shift+C` / `Cmd+Shift+C` during review) to copy full file paths.

For standard AnkiConnect action documentation, see the [AnkiConnect](https://foosoft.net/projects/anki-connect/) project page.
