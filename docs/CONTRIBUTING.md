# Contributing to arete

One maintainer. Commit to `main` and push. No branch, no pull request.

If you are an outside contributor, fork the repo and open a pull request. The rest of
this file applies to you too.

## The one gate

```bash
just qa
```

It runs, in order:

| Step | Command |
|---|---|
| Python autofix | `just fix` |
| Types | `just check-types` |
| Import contracts | `just check-architecture` |
| Docs references | `just check-docs` |
| Python tests | `just test` |
| Plugin format | `npm run format` |
| Plugin tests | `just test-obsidian` |
| Plugin lint | `just lint-obsidian` |
| Plugin build | `just build-obsidian` |

**If `just qa` passes, push.**

`just qa` does not run `tests/integration`. Those need Docker. Run them when you touch
the sync path, the bridge, or anything the container exercises:

```bash
just test-integration
```

## Setup

```bash
uv sync
uv run pre-commit install
cd obsidian-plugin && npm install
```

## Individual commands

| Task | Command |
|---|---|
| Lint and format Python | `just lint` |
| Autofix Python | `just fix` |
| Type check | `just check-types` |
| Python unit tests | `just test` |
| Integration tests | `just test-integration` |
| Build the plugin | `npm run build` |
| Plugin tests | `npm test` |
| Plugin lint | `npm run lint` |

## Testing

Every fix gets a test that fails without it.

| Suite | Location | Scope |
|---|---|---|
| Python unit | `tests/`, mirroring `src/arete/` | One function or class. External systems mocked. |
| Python e2e | `tests/e2e/` | A whole sync against a collection in `tmp_path`. No container. |
| Python integration | `tests/integration/` | The bridge over AnkiConnect, against a container. |
| Plugin | `obsidian-plugin/tests/` | UI logic, settings parsing, command invocation. |

The plugin mocks Obsidian's API at `obsidian-plugin/tests/obsidian-mock.ts`, wired
through `moduleNameMapper` in `jest.config.js`.

> [!warning] Never point a test at a collection you study from
> `tests/conftest.py` gives every test a throwaway `HOME`, and a session guard fails
> the run if a real `collection.anki2` moves. Setting `ANKI_CONNECT_URL` bypasses the
> container and defeats the first of those. The guard fires after the write, not
> before.

## Architecture

- **One-way sync.** Obsidian is the source of truth. We push to Anki and never pull back.
- **The plugin wraps the CLI.** Keep logic in the CLI, where a test can reach it. The
  plugin handles the interface and the process.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the system design.
