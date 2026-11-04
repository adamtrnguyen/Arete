# The QA gate: concurrent, read-only, pinned, enforced on push (2026-11-04)

The fleet audits of 2026-11-04 (`/tmp/project-audits/arete.md`, gap G3) measured Arete's
`just qa` against the fleet standard. This records what they found and what replaced it.

## What the audit found

- `qa` ran nine lines in sequence under `sh -cu`. A just recipe stops at its first failing
  line, so a lint error hid the type check, the import contracts and both test suites.
- It began with `just fix` and `npm run format` (prettier `--write`). The gate rewrote the
  tree it was meant to judge, and prettier rewrote the generated `coverage/` report on
  every run.
- It had no format check, no `check-versions` (that ran only in `verify`), no dead-code
  check and no plugin type-check.
- `.pre-commit-config.yaml` existed but was never installed: `.git/hooks` held only
  `*.sample`. Its ruff `rev` was `v0.5.0` while uv.lock held 0.15.20, and it listed gate
  checks (ts-lint, py-test, ts-test) instead of running the gate.
- Tool versions floated: `ruff>=0.5.0`, `pyright>=1.1.408`, and `ty>=0.0.8` (0.0.55
  installed) although no gate ran ty.
- CI did not run `lint-imports`.
- Two recipes were broken: `dev-backend` started `arete.interface.main:app`, a module that
  does not exist, and `test-migration` called `arete migrate`, a command that does not
  exist.

## What changed

- `qa` is the fleet's concurrent template. Eleven checks run at once and every failure is
  named: lint, fmt-check, types (pyright), layers (lint-imports), dead (vulture), test,
  check-docs, check-versions, lint-obsidian, types-obsidian (`tsc --noEmit`),
  test-obsidian. It writes nothing; the fixers moved to `just fix`.
- `verify` (the release gate) is now `qa`, so a release and a push pass the same checks.
- `check-types` and `check-architecture` remain as aliases of `types` and `layers`.
- Pins: `ruff==0.17.2`, `pyright==1.1.411`. `ty` was removed: Arete gates on pyright and
  the fleet keeps one checker per repo.
- `[tool.vulture]` at 80% confidence, with five protocol-required parameter names ignored
  (pydantic-settings' `settings_customise_sources`, a context manager's `__exit__`).
- Ruff select names `C901` instead of the `C90` family (same rule).
- Pre-commit: the fleet template. Ruff hooks at `v0.17.2` (`ruff-check`, `ruff-format`),
  gitleaks, and one pre-push hook, `entry: just qa`. Installed with `uvx pre-commit install`.
- CI's lint job runs `just layers`.
- `dev-backend` runs `arete serve daemon --reload`; `test-migration` was deleted.

## The C901 waivers

The `pyproject.toml` comment over the per-file C901 waivers said "18 functions are over the
limit of 10; the worst is run_pipeline at 30" (written at `43b090e`, 2026-09-08). The
2026-11-04 audit measured 17 functions, `run_pipeline` at 38, `_prune_orphans` at 23 and
`consumer` at 15: four sync commits on 2026-09-25 grew them under the waiver. One waiver
named `application/card_editor.py`, a file that no longer exists; it was removed. The
comment now names the command that measures the debt instead of a number.

## Integration tests

Integration tests (Dockerized Anki) stay out of `qa`: they need OrbStack and are flaky.
Run `just test-integration` when a change touches the sync path or the bridge.
