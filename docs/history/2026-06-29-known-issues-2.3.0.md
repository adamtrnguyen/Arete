# Known issues opened by the 2.3.0 deps refresh — and what became of them

Recorded 2026-06-29. Every item re-verified against the machine on **2026-09-10**.

This list lived in `CLAUDE.md` for two and a half months. Five of its entries were
fixed and never struck through, so a reader loading that file got told about bugs
that no longer existed. The list moves here; `CLAUDE.md` keeps only what is open.

## Why the list existed

The items were all **pre-existing**. They shipped in 2.1.0 too. CI hid them: it died
at the install step, `uv sync --extra agent`, on an extra that no longer exists. The
pipeline fix, `--extra agent` to `--dev`, made them visible. None blocked 2.3.0.

## Resolved

| Item | Verified by |
|---|---|
| anki 25 sync round-trip untested. Fixed in 2.4.0. | The hermetic e2e suite, plus a 3574-card sync against live Anki 25.09. |
| Ruff lint gate could never pass. `[tool.ruff.lint]` selected `D203`+`D211` and `D212`+`D213`, two exclusive pairs. Fixed in 2.4.0. | — |
| Plugin Jest failures, 7 tests, in `StatsService.test.ts` and `CardParserService.test.ts`. | `npm test`: 21 suites, 158 tests, 0 failures. |
| The plugin read FSRS difficulty as 1-10. The backend sends 0.0-1.0, so every threshold was dead. | `CardVisualsService.ts:38,41,57,58` now compare against `0.8`, `0.5`, `0.9`. Line 61 converts for display with `difficultyOutOfTen()`. |
| The plugin still offered backend `apy`, which `AppConfig` rejects. | `grep -rn apy obsidian-plugin/src/` returns nothing. |
| "Integration suites cannot run anywhere." Both compose files and the integration conftest named `ghcr.io/adanato/arete/anki-custom:latest`. | A **namespace typo**, not a missing publish. The image sits under `adamtrnguyen`, the repo owner. `docker manifest inspect` resolves it, linux/amd64. Fixed in `9a0d0fb`. The suite runs: 51 passed, 1 xfailed. |

The last row is the one worth remembering. The recorded diagnosis said the image "was
never published". That was wrong, and it stood for two and a half months. A
`manifest unknown` from a wrong account name reads exactly like a `manifest unknown`
from an absent image. Nobody checked the account against `git remote -v`.

## Still open on 2026-09-10

These moved to `CLAUDE.md` without their dates. Listed here for the record:

- `arete_ankiconnect/manifest.json` reads 2.2.1 while `pyproject.toml`, the plugin
  manifest and the plugin package all read 2.4.0.
- The dangling `v2.2.1` tag on origin. `git ls-remote --tags origin` still shows it.
- PyPI trusted publishing is unconfigured. `release.yml:53` runs the publish step
  under `continue-on-error: true`, so it cannot block a release.
- Reconcile-by-Arete-id lives in the AnkiConnect adapter only. A strict xfail at
  `tests/e2e/test_local_sync.py:119` records the gap.
- `arete vault check` does not detect a duplicate Arete id.
- The three surfaces duplicate their wiring.
- GitHub Actions versions are behind (Dependabot PR #48).

## Not verifiable from this machine

Three Windows-only failures: `test_common.py::test_to_list_path`,
`test_models.py::TestAnkiNote::test_to_dict_converts_path` (both emit OS separators
where a vault path should stay POSIX), and
`test_graph_resolver.py::...resolves_nfd_filename_with_nfc_ref`
(`UnicodeDecodeError` reading an NFD filename). macOS and Ubuntu pass. These need a
Windows runner to confirm or clear.
