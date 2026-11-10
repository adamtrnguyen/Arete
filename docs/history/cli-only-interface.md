# CLI-only interface (4.0.0)

Release 4.0.0 made the CLI Arete's only interface.

## Removed

- Plugin server mode: the "Execution mode" and "Server port" settings, the start, stop and restart server commands, and `ServerManager`.
- The HTTP server and the `arete serve` command group (`arete serve daemon`, `arete serve mcp`).
- The MCP server (`src/arete/interface/mcp_server.py`, the `mcp__arete__*` tools, the repo's `.mcp.json` entry).

## Why

The owner's words: "dealing with a server is too complicated" and "i don't think i've ever had a proper usecase for mcp".

## What replaced them

| Command | Output |
| :--- | :--- |
| `arete anki decks` | `{"decks": [...]}` |
| `arete queue PATH --dry-run --json` | deck, due_count, total_with_prereqs and the queue list |
| `arete anki queue-deck --ids JSON [--deck-name] [--no-reschedule]` | `{"ok", "message"}` |
