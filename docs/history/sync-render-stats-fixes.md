# Sync, render, stats and logging fixes (2026-09-25)

From the 2026-09-25 bug hunt. Each fix came with a test that failed before it. Moved here
from source comments on 2026-11-04; the comments keep the rule.

## Sync (`9e9655a`, `a7054cc`)

- **C3, warm re-sends** (`application/sync/parser.py`). A stat-cache hit appended the
  cached note to `notes`, so an unchanged file re-sent every card on every warm run. The
  hot-cache entry is only written after Anki accepts a note, so a hit now sends nothing
  and only feeds the prune inventory.
- **B1, Arete keys as fields** (`parser.py`). Custom-model cards sent `id`, `deps`,
  `anki` and `__line__` as Anki fields; being in the hash, they re-synced a card whenever
  its line moved.

## Render (`a7054cc`, `application/sync/converter.py`)

All 8,044 real card fields were rendered before and after: 3 changed in substance (the 3
that showed placeholder text) and 256 more only by `<`/`>` inside math now being escaped.

- **R1.** Math followed by a digit (`$\sim$100x`, `$n$1`) leaked `MATH-PLACEHOLDER-0100x`
  onto 3 cards. The placeholder token is now terminated.
- **R2.** A stray backtick (``don`t``) opened a code span that never closed and switched
  math parsing off for the rest of the field. A run now opens a span only if it closes in
  the same paragraph.

## Stats (`9e9655a`, `3158c5d`)

- **S2** (`domain/stats/models.py`). Revlog learning steps are negative seconds and were
  mixed with day intervals: a lapse gave an interval growth of -20 and a volatility of
  88200. `revlog_interval_days` converts them.
- **FSRS answers** (`infrastructure/adapters/stats/connect_stats.py`). The add-on's
  `getFSRSStats` sent no stability until `3158c5d`, and `connect_stats` required it, so
  every answer was discarded and difficulty came only from a `cardsInfo` fallback.

## Logging (`30c3b32`, `application/utils/logging.py`)

`rotate_logs` was defined and tested but never called, so every run added a log forever:
2,104 files, 295 MB in `~/.config/arete/logs` on 2026-09-25. `setup_logging` calls it.
