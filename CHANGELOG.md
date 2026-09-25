# Changelog

## Unreleased

### Added

- **Changing a card's `model` converts its Anki note in place,** keeping its cards
  and review history. Before, a card whose model no longer matched its note (say,
  Cloze in the vault and Basic in Anki) silently stopped updating. Fields carry over
  by name, then Front↔Text and Back↔Back Extra. Over AnkiConnect this needs the
  add-on from this release (new `changeNoteType` action).

### Corrected

- The 3.0.0 notes said the next sync re-sends every card. It does not: unchanged
  files are skipped before hashing. Run `arete sync --clear-cache` once to rewrite
  every note.

## 3.0.0

### Added

- **Organized highlighting of the `cards:` block in Source mode.** Each card sits on its
  own band; question keys (`Front`, `Text`) are blue with a bold value, answer keys
  (`Back`, `Back Extra`) green, `deps` purple, and `id`/`anki`/`model` dimmed. Cloze
  deletions get a chip colored by `cN`; `$math$` is tinted. Text inside a `|-` block is
  never mistaken for a key.

### Fixed

- **The plugin's Check File, Fix, suspend and unsuspend failed in CLI mode** with
  "No such command": they called commands removed in 2.4.0.
- **`arete vault fix` moved `nid:` out of a synced card's `anki:` block.**
- **Card stats never used the add-on's FSRS answer.** The add-on did not send
  stability and arete required it, so difficulty came only from a fallback.
  Update the Anki add-on to get stability.

### Removed

Breaking. Every note in a real vault was checked first; none relied on these.

- A note must have `arete: true`. A note with `cards:` and a deck but no marker was
  still synced.
- Lowercase field names (`front`, `back`, `text`, `extra`) and `Extra` for Cloze. Use
  `Front`, `Back`, `Text`, `Back Extra`.
- Card-level `nid`, `cid` and `markdown` keys. Arete keeps ids under `anki:`.
- `~/.arete.toml`. The config file is `~/.config/arete/config.toml`.
- The `O2A_` environment prefix, from the project's old name. It is `ARETE_` now,
  e.g. `ARETE_ANKI_CONNECT_URL`.
- The plugin's "Arete Script Path" setting (the old `arete/main.py`). Use Python
  Executable and Project Root.
- Anki add-on fallbacks for Anki versions from before FSRS.

### Changed

- **Prune over AnkiConnect checks every note in an Arete deck,** as the direct
  backend always did. It used to see only notes whose type had an `nid` field
  (the old `O2A_Basic`), so Basic and Cloze orphans were never found. A note you
  made by hand in an Arete deck is now a prune candidate; prune still lists
  everything and asks first.
- **One full re-sync after upgrading.** The content hash no longer renders the
  card in apy's editor-note format, and fields lose the unused
  `<!-- arete markdown -->` comment, so every card is re-sent once. Fields are
  updated in place; review history is kept. The add-on reads only the 4-part
  `vault|path|line|id` source link, which the re-sync writes to every note.

## 2.5.0

### Added

- **Card preview in the Obsidian YAML editor.** The Preview pane now renders the
  actual Anki card — the model's own CSS and card template, with fields run
  through the Markdown renderer so MathJax works — with a Front/Back toggle. The
  card is drawn inside a sandboxed iframe, so the model CSS cannot leak into the
  rest of the UI, and card images and links resolve against the vault.
- **Cloze and type-in cards preview too.** `{{cloze:Text}}` shows the active
  deletion hidden on the front and revealed on the back; `{{type:Field}}` shows an
  answer box. Math inside a deletion still renders.
- **`obsidian://arete?vault=…&file=…&card=<id or position>`** opens a note at one
  card in the card editor, so an agent in a terminal can show you the card it wrote.
- **The card editor follows edits made outside Obsidian.** It reloaded before
  Obsidian re-parsed the file, so an agent's edit appeared one edit late.
- **`arete graph export`** and `POST /graph` return the resolved dependency graph.
  The plugin's graph views now draw that instead of resolving references
  themselves, so they get every resolver fix below.

### Fixed

- **Sync could lose notes and review history.** Prune deleted notes created in the
  same run, deleted the notes of cards that failed validation, and ran while files
  could not be read. A failed or dry-run sync marked cards as sent. The id
  write-back matched cards by position, not id.
- **Card rendering:** `$n$1` leaked a math placeholder; stray backticks, `>` in
  callout math and `<` in math broke cards; a `---` inside a field split the
  frontmatter.
- **Dependency graph and queue:** a cycle anywhere scrambled the study order;
  prerequisites were collected depth-first; a deck filter matched name prefixes;
  a note name shared by two files resolved to one at random; scalar and numeric
  refs were dropped; duplicate card ids overwrote each other silently.
- **Stats:** difficulty fell back to the wrong scale; review intervals mixed
  seconds and days; "Gain" divided by a learning step (x288); card tag edits and
  an empty card `deck:` were ignored; an unchanged vault re-sent every card.
- **Obsidian plugin views:** the global graph never drew (no stylesheet; its "Fit"
  button only re-rendered); hidden graph views re-rendered every 100 ms; the local
  graph showed another note's graph for unsynced cards; the due badge read day
  numbers as timestamps ("20721d ago"); dependency chips were unstyled.
- **MCP servers hid failure reasons** after the mcp 2.x migration: a tool error
  now says why it failed.

- **The plugin asked the CLI for model data under the wrong command names.** It
  sent `models-styling` / `models-templates`; the CLI registers `model-css` /
  `model-templates`. The card preview could never fetch a model.
- **Server mode never started.** The plugin spawned `arete server --port`, but
  there is no `server` command — it is `arete serve daemon`. The `--reload` flag
  was also passed before the subcommand instead of after it.
- **The card renderer passed a null `Component` to `MarkdownRenderer`.**

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
