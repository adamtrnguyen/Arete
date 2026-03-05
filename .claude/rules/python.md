---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "scripts/**/*.py"
---

# Python Development Rules

## Before Editing

- Use `LSP hover` to check type signatures before modifying a function
- Use `LSP findReferences` before renaming or deleting anything
- Use `LSP goToDefinition` to follow imports instead of grepping

## Code Style

- Python 3.12+, type hints on all function signatures
- Line length: 100 (enforced by ruff)
- Use `pathlib` over `os.path`
- Prefer Pydantic models over raw dicts
- Async throughout — use `async def` for I/O operations

## Architecture (DDD Layers)

IMPORTANT: Respect the layered architecture. Violations fail CI.

```
interface → application → infrastructure → domain
```

- **domain/** imports NOTHING from other layers
- **application/** uses interfaces (ABCs), never concrete adapters
- **infrastructure/** implements domain interfaces
- **interface/** orchestrates application services

Verify with: `just check-architecture`

## Testing

- Unit tests don't need Anki — mock the `AnkiBridge` interface
- Use `pytest` fixtures from `tests/conftest.py`
- Match test directory to source directory (e.g., `tests/domain/` for `src/arete/domain/`)
- Run `just test` after changes, not `pytest` directly

## Quality Gate

After making changes, run:
```bash
just fix           # ruff fix + format
just check-types   # pyright
just test          # unit tests
```

Before committing: `just qa` (runs everything)
