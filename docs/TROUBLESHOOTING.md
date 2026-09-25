# Troubleshooting Guide

This guide covers common issues encountered while using ObsiAnki (arete) and how to resolve them.

## 1. Connection Issues (AnkiConnect)

If the sync fails with a "Cannot connect to Anki" error:

-   **Is Anki running?**: Anki must be open for the sync to work.
-   **AnkiConnect installed?**: Ensure you have the [AnkiConnect](https://ankiweb.net/shared/info/2055492232) add-on installed in Anki.
-   **Port Binding**: By default, AnkiConnect listens on `127.0.0.1:8765`. 
-   **Allowed Origins**: In Anki, go to `Tools -> Add-ons -> AnkiConnect -> Config` and ensure your origin is allowed. For arete, adding `*` to `webBindAddress` (if needed) or ensuring `localhost` is in `webCorsOriginList` helps.

## 2. WSL (Windows Subsystem for Linux)

WSL users often face networking hurdles because WSL 2 uses a virtualized network.

-   **The `curl.exe` Bridge**: `arete` attempts to use `curl.exe` (the Windows host binary) to communicate with Anki running on Windows. Ensure `curl.exe` is in your Windows PATH and accessible from WSL.
-   **Firewall**: Ensure Windows Firewall isn't blocking incoming connections to port 8765.

## 3. Python Environment (`uv`)

-   **`Command not found: arete`**: Ensure you are running commands with `uv run arete` or that your virtual environment is activated.
-   **Dependency Errors**: Run `uv sync` to ensure all dependencies are correctly installed.

## 4. Sync Gaps & Missing Cards

-   **"File skipped"**: If a file isn't syncing, check if it has the required frontmatter or tags (depending on your config).
-   **Cache Mismatch**: If you suspect the cache is out of sync, you can force a re-scan by deleting the cache file:
    ```bash
    rm ~/.config/arete/cache.db
    ```
    *(Path may vary based on OS; use `arete config show` to find your config directory).*

## 5. Locating Logs

When all else fails, check the logs. They contain detailed stack traces and internal logic transitions.

```bash
uv run arete --open-logs
```

This will open the directory containing your `run_*.log` files. Attach the latest log to any GitHub issue you open.
