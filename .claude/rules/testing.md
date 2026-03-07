---
paths:
  - "tests/**"
---

# Testing Rules

## Test Categories

| Directory | Requires Anki | What |
|-----------|--------------|------|
| `tests/domain/` | No | Domain model tests |
| `tests/application/` | No | Use case / service tests |
| `tests/infrastructure/` | No | Adapter unit tests (mocked) |
| `tests/interface/` | No | CLI + MCP server tests (mocked) |
| `tests/integration/` | **Yes** | Full sync/bridge tests against real Anki |
| `tests/e2e/` | **Yes** | End-to-end scenario tests |

## Running Tests

- Unit tests: `just test` (no Anki needed, mock `AnkiBridge`)
- Integration tests: `just mac-docker-up && just wait-for-anki && just test-integration`
- Match test directory to source directory (e.g., `tests/domain/` for `src/arete/domain/`)
- Use `pytest` fixtures from `tests/conftest.py`

## Docker (OrbStack)

Integration/e2e tests use a headless Anki 24.11 container via OrbStack.

| Command | What |
|---------|------|
| `just mac-docker-up` | Start OrbStack + Anki container |
| `just wait-for-anki` | Poll until AnkiConnect responds |
| `just docker-down` | Stop container |

### Port Convention

| Context | Port |
|---------|------|
| Docker (tests) | `http://127.0.0.1:8766` |
| Local Anki (production) | `http://127.0.0.1:8765` |

Override with `ANKI_CONNECT_URL` env var.

## Known Issues

- `test_parser_adds_obsidian_source` is a known failing test (pre-existing)
- Agent/MCP tests require optional deps — skip with `--ignore=tests/application/test_agent.py --ignore=tests/interface/test_mcp_*`
