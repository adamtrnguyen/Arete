# Prune ownership, prune confirmation and path containment, 2026-11-04

An engineering audit (gap G1) found that the agent and HTTP surfaces could delete Anki
decks the vault never owned, without a confirmation. This records what was found, what
changed, and what now prevents it.

## What the audit found

- `_prune_orphans` treated every note in every non-Default deck as an orphan unless the
  scanned vault claimed it. It did not ask whether Arete had made the note.
  `tests/e2e/test_local_sync.py` asserted this on purpose: hand-added notes were deleted.
- The only confirmation was `if not config.force: input(...)`. `force` meant both
  "ignore the cache" and "skip the prompt". The MCP description of `sync_vault` said
  only "Force sync all notes, ignoring cache".
- Off a terminal (the MCP stdio server, the uvicorn daemon) a prune without `force`
  called `input()` on a stream that was not a person.
- `sync_vault(vault_path)` and HTTP `/sync` accepted any directory. `resolve_config`
  then made that directory the vault root, so the "prune needs the whole vault" check
  passed for any directory at all.
- `get_note_body` and `list_file_cards` read any absolute path. `sync_file` wrote
  `anki.nid` into any Markdown file. Every HTTP route that opens Anki let the request
  choose `anki_base`, which the direct backend unpickles `prefs21.db` from.
- The probe: a throwaway collection with decks `Personal::Japanese`, `Work` and
  `Default`, synced with `prune=True, force=True` against a one-file vault, printed
  `deleted: ['Personal::Japanese', 'Work']`.

## What changed

**Ownership.** A note is Arete's only if it carries an Arete id tag: `arete_` plus a
26-character ULID, as `id_service` generates and the parser sends
(`domain/ownership.py`). Prune deletes such a note when the vault no longer claims it.
A deck goes only when every note in it, subdecks included, is such an orphan; it is
re-checked for emptiness after the notes are deleted. Empty decks are listed and left:
Anki records no creator for a deck.

The format test is strict on purpose: a hand-made tag such as `arete_review` must not
make a note deletable. On 2026-11-04 the live vault held 3929 card ids, of which 277
do not match the format (hand-written ids such as `arete_laudan_01`, or ULID-shaped
ones containing `L` or `U`). Their Anki notes are no longer prunable.

**Confirmation.** `force` now only re-sends cards. Deleting needs `--yes` (CLI),
`confirm_prune=true` (MCP, HTTP), or "yes" typed at a terminal prompt. The prompt is
shown only when stdin is a terminal; otherwise the run returns the preview (status
`unconfirmed`, the notes and decks it would delete, with counts) and deletes nothing.
The confirmation is a per-run argument to `execute_sync`, not an `AppConfig` field, so
no config file or environment variable can pre-approve a prune.

**Containment.** `application/vault_paths.py` resolves every caller-supplied path on
the MCP and HTTP surfaces and refuses one outside the configured vault (`vault_root`
from the config file or `ARETE_VAULT_ROOT`; the plugin sets the latter on the daemon it
starts). `sync_vault(vault_path)` now syncs that folder inside the configured vault, so
a prune on anything less than the whole vault is skipped. A request's `anki_base` is
refused unless it names the configured one. The CLI is not fenced.

**Tests cannot reach a running Anki.** `tests/conftest.py` refuses every socket to port
8765. Before it, three tests in `tests/application/test_main_components.py` probed the
live AnkiConnect when Anki was open (a `version` call; their pipeline was mocked).
