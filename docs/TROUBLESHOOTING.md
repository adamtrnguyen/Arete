# Troubleshooting Guide

This guide covers common issues encountered while using Arete and how to resolve them.

## 1. Connection Issues (AnkiConnect)

If Arete cannot reach Anki:

-   **Anki closed?**: Arete writes the collection directly (the default `auto` backend).
-   **Anki open?**: Arete needs the `arete_ankiconnect` add-on ([ANKI_PLUGIN.md](ANKI_PLUGIN.md)). It replaces AnkiConnect: do not install both.
-   **Port Binding**: The add-on listens on `127.0.0.1:8765` (`webBindAddress`, `webBindPort`).
-   **Allowed Origins**: In Anki, `Tools -> Add-ons -> arete_ankiconnect -> Config`: make sure your origin is in `webCorsOriginList` (`http://localhost` by default).

## 2. WSL (Windows Subsystem for Linux)

WSL users often face networking hurdles because WSL 2 uses a virtualized network.

-   **The `curl.exe` Bridge**: `arete` attempts to use `curl.exe` (the Windows host binary) to communicate with Anki running on Windows. Ensure `curl.exe` is in your Windows PATH and accessible from WSL.
-   **Firewall**: Ensure Windows Firewall isn't blocking incoming connections to port 8765.

## 3. Python Environment (`uv`)

-   **`Command not found: arete`**: Ensure you are running commands with `uv run arete` or that your virtual environment is activated.
-   **Dependency Errors**: Run `uv sync` to ensure all dependencies are correctly installed.

## 4. Sync Gaps & Missing Cards

-   **"File skipped"**: If a file isn't syncing, check that its frontmatter has `arete: true` and a non-empty `cards:` list. `arete vault check <file>` says what is missing.
-   **Cache Mismatch**: If you suspect the cache is out of sync, you can force a re-scan by deleting the cache file:
    ```bash
    rm ~/.config/arete/cache.db
    ```
    *(Path may vary based on OS; use `arete config show` to find your config directory).*

## 5. Locating Logs

When all else fails, check the logs. They contain detailed stack traces and internal logic transitions.

```bash
uv run arete logs
```

This will open the directory containing your `run_*.log` files. Attach the latest log to any GitHub issue you open.
