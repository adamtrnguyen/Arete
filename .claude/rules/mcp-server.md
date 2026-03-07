---
paths:
  - "src/arete/interface/mcp*"
---

# MCP Server Rules

FastMCP-based server exposing Arete tools to AI agents. Entry point: `uv run arete serve mcp` (stdio transport).

## Available Tools

| Tool | What it does | Needs Anki |
|------|-------------|-----------|
| `sync_vault` | Sync vault to Anki | Yes |
| `sync_file` | Sync a single file | Yes |
| `get_stats` | Learning statistics + leeches | Yes |
| `browse_concept` | Open Anki browser for a concept | Yes |
| `browse_card` | Open Anki browser for a specific card | Yes |
| `get_concept_cards` | Read card content from vault markdown | No |
| `get_due_cards` | Show due cards with Arete IDs | Yes |
| `build_study_queue` | Build dependency-ordered filtered deck | Yes |

## MCP Config

```json
{
  "mcpServers": {
    "arete": {
      "command": "uv",
      "args": ["run", "--project", "/Users/adam/Research/arete", "arete", "serve", "mcp"]
    }
  }
}
```
