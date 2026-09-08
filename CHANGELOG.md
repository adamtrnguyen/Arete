# Changelog

## 2.4.0

A correctness release. Several bugs in this list could put cards in the wrong
place, or create copies of cards you already had, without telling you.

### Fixed

- **Notes were filed into `Default` instead of the deck their note declares.**
  The bundled AnkiConnect add-on set the deck on a copy of the note type and
  then called Anki's legacy `addNote`, which on Anki 25 reads the note type's
  stored deck. It now passes the deck to `collection.add_note` directly.
- **`--dry-run` was not dry.** It called Anki, wrote the cache, and rewrote
  files to assign Arete ids. Only the id write-back and the prune stage
  honoured the flag. A dry run now changes nothing and reports what it would do.
- **Sync created copies instead of finding the card it had already written.**
  A card whose note id was missing or stale was matched only within its target
  deck, so a copy that had landed elsewhere was invisible. Sync now looks up the
  card by its Arete id tag across the whole collection, then by content, and
  moves the card it finds to the right deck. A stale note id is logged instead
  of silently becoming a new note.
- **`arete report --clear` reported an unsuspend that had failed.** It printed
  "unsuspended N cards" while the cards stayed suspended. It now reports the
  failure, names the cards, gives the command to unsuspend them, and exits 1.
- **A failed statistics lookup marked every card "mature",** which blocked
  deletes and warned on edits to brand-new cards. It now reports "unknown".
  Anki being closed still means "mature", which is the safe reading.
- **An AnkiConnect outage produced an empty study queue** and the message
  "nothing due". The error now surfaces.
- **A note that could not be parsed vanished from the dependency graph**
  with only a log line. `arete graph check` now lists the files it could not
  read and reports the graph as unhealthy.
- **A corrupt `reports.json` silently became an empty report list.**
- **`--max-cards` was ignored by the default queue algorithm,** which always
  used 50. Which prerequisites survived the cap also depended on set iteration
  order, so it varied between runs. It is now sorted and deterministic.
- **The two backends reported FSRS difficulty on different scales.** Both now
  return the 0.0 to 1.0 the domain model documents. Learning insights report
  the card's deck instead of its note type.
- **The setup wizard offered a backend named `apy`,** which the config rejects.
  It is `direct`.

### Changed

- `AnkiBridge` is now four role ports: `SyncPort`, `QueuePort`, `CardStatsPort`
  and `AnkiAdminPort`. `AnkiBridge` remains as the composite that adapters
  implement. Each use-case takes only the role it uses.
- The adapter owns its own concurrency. `AnkiBridge.is_sequential` is gone.
- `vault format` and the HTTP and MCP servers use the same cache as `sync`.
  They previously read a second database, so they could disagree.
- The cache carries a schema version and rebuilds itself on a mismatch. Your
  existing cache rebuilds once on the first run of this version, which is a
  full re-read of the vault and no re-sync.
- `--algo` offers `simple` alongside `static` and `dynamic`. It always accepted
  `simple` and never listed it. The HTTP and MCP surfaces take `algo` too.
- Every exception handler that used to swallow a failure now logs it.

### Removed

Breaking, though nothing in the Obsidian plugin used any of it.

- CLI flag `--include-related`. It parsed the flag and then exited with
  "not implemented yet".
- Config fields `run` (`sync_enabled`), `no_move_deck`, `show_config`,
  `open_logs`, `open_config`. Nothing read them. Unknown keys in `config.toml`
  are ignored, so an old file still loads.
- The `ANKI_CONNECT_HOST` environment variable. The URL has one source now:
  `--anki-connect-url`, `config.toml`, or `O2A_ANKI_CONNECT_URL`.
- Port methods with no caller: `ContentCache.set_hash` and `get_file_meta`,
  `AnkiBridge.get_model_names`, `ensure_deck` and `find_all_arete_nids`.
- 613 lines of source that nothing reached: the snapshot module, the queue
  session writer, the weak-prerequisite queue path (its criteria object was
  never constructed, so it never filtered anything), a duplicate id assigner,
  two unused math helpers, an unreachable report section, and five unread
  configuration and result fields.

### Testing

- A hermetic end-to-end suite. It drives the real config, composition root,
  pipeline and adapter against a real Anki collection built in a temp
  directory. It needs no Docker, no network and no running Anki, and takes
  under a second. The 2645 lines of integration tests that came before it have
  never run, because each waits on a container image that was never published.
- Fixtures drawn from what a real vault contains: three note types, nested
  decks, a card-level deck override, math, an image embed, a callout, a code
  fence, a dependency pair and an accented filename.
- `ARETE_TEST_VAULT` points the invariant check at a real vault. It parses
  only, and asserts that everything parses, that no card falls back to
  `Default`, and that an Arete id identifies one card.
- Tests can no longer touch `~/.config/arete`. A guard fails the run if they do.
  A test run had previously wiped a real cache.

### Internal

- `factory` and `orchestrator` moved to `arete/composition/`. The import
  contracts could not see the adapter packages before, because four of them had
  no `__init__.py`, so the rule against the application layer reaching
  infrastructure had never been enforced. Four contracts now hold.
- The lint gate passes. It could not before: the configuration selected two
  mutually exclusive pairs of docstring rules. Complexity debt is pinned file by
  file, so a new complex function still fails.
