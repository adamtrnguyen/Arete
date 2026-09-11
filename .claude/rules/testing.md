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
- Integration tests: `just test-integration`. The session starts its own container.
- Match test directory to source directory (e.g., `tests/domain/` for `src/arete/domain/`)
- Use `pytest` fixtures from `tests/conftest.py`

## Docker (OrbStack)

`tests/integration` uses a headless Anki container. Start OrbStack, then run
`just test-integration`. The session starts the container on a random free port and
stops it after. `tests/e2e` needs no container.

The `docker-up`, `wait-for-anki` and `docker-down` recipes exist for CI, which exports
`ANKI_CONNECT_URL` first so the conftest reuses the compose container. Do not run them
by hand: without that variable you get two containers.

### Port Convention

| Context | Port |
|---------|------|
| Integration container | random, assigned per session |
| Local Anki (production) | `http://127.0.0.1:8765` |

`tests/integration/conftest.py` picks a free port for the container it starts. Set
`ANKI_CONNECT_URL` to skip Docker and use an existing instance. 🛑 Never point it at a
collection you study from.

## Known Issues

None recorded. The two that used to sit here were both stale, checked 2026-09-10:
`test_parser_adds_obsidian_source` passes, and `tests/application/test_agent.py` does
not exist, because the `agent` extra was removed. `tests/interface` runs 98 tests with
no optional dependency.
