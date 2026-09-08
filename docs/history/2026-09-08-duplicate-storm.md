# The duplicate storm, 2026-09-07

Anki held 10036 notes. 7026 of them belonged to no card in the vault, and 6814 of
those were exact copies of a note that did. The owner had not noticed, because
nothing ever said so.

This is the record of what caused it, what was fixed, and what now prevents it.
Numbers here are measurements taken on the night, not estimates.

## What the collection looked like

| | Count |
|---|---|
| Notes in Anki | 10036 |
| Notes a vault card pointed at | 3010 |
| Orphans | 7026 |
| Orphans that copied a linked note exactly | 6814 |
| Orphans with any review history | 9 |
| Vault ids pointing at a note that no longer existed | about 551 |

The orphans arrived in two bursts, 3790 in February 2026 and 3220 in March. Both
bursts predate the session; a third burst of 1653 happened during it, described
below.

## Four faults, each necessary

No single bug did this. Four independent faults lined up.

**1. The deck was ignored.** The bundled AnkiConnect add-on resolved the target
deck, wrote its id onto a *copy* of the note type, then called Anki's legacy
`collection.addNote(note)`. On Anki 25 that reads the note type's stored deck,
which is `Default`. Every note created through the add-on went to `Default`
regardless of what its note said. Reproduced directly: a bare `addNote` aimed at
`AI::Deep Learning::Transformers` landed in `Default` for both Basic note types.

**2. Sync could not find what it had already written.** When a card had no note
id, or an id Anki no longer knew, the adapter searched for an existing match with
`"deck:<target>" "note:<model>"`. Because of fault 1 the copy was sitting in
`Default`, outside the search. The card's own Arete id tag was written to the note
but never searched. So sync created the card again. Every run.

**3. `--dry-run` was not dry.** The flag was checked in two places: the id
write-back and the prune stage. The stage that calls Anki was not one of them. A
dry run created every card that lacked an id. Two dry runs during the session
created 555 notes each. The integration test named `test_sync_dry_run` asserted
only that no id appeared in the markdown, so it passed throughout.

**4. Nothing said anything.** Seventeen exception handlers swallowed their error
with `pass`, `continue`, or an empty return. The paths that matter here logged at
debug or not at all.

## The night's own burst

Diagnosis added 1653 notes to the pile: two dry runs at 555 each, then one real
sync. All three went to `Default`. They were deleted the same night after the
window was identified from the run logs. Anki's own backup from 23:09 predates
the deletion. This is why fault 3 is written up as a defect and not a footnote:
the safe command was the dangerous one.

## What changed

Commits `b558b57` through `43b090e`.

- The add-on passes the deck id to `collection.add_note(note, deck_id)`.
- Reconcile order for a card without a usable id: the Arete id tag across the
  whole collection, then normalized content within the note type, then create. A
  match is moved to the right deck rather than duplicated. A stale id is logged.
- `--dry-run` gates the Anki call, the cache write, and the id file rewrite.
- The hot cache is refreshed with the id Anki assigned. It previously kept
  `nid=None`, so a warm run re-sent every card as new.
- Every silent handler logs.

## Cleanup

6814 notes were deleted: arete-created, matching a linked note exactly, and never
reviewed. Kept: 7 reviewed twins in `Art::Anatomy`, and 180 orphans with unique
content in decks no vault note declares. Anki went from 10036 notes to 3748, of
which 3561 are vault-linked.

## What stops it happening again

A test, not a note in a file.

- `tests/e2e/test_local_sync.py` syncs into a real Anki collection in a temp
  directory and asserts the deck each card declares. Mutation-tested: pointing
  the repository's `add_note` at `Default` fails it.
- The same suite asserts that a second sync creates nothing, and that a dry run
  changes neither Anki nor the vault.
- A strict xfail records the remaining half: reconcile-by-tag exists in the
  AnkiConnect adapter only, so the direct backend still duplicates on a stale id.
  It fails the day someone fixes it.
- `ARETE_TEST_VAULT` runs the invariant check over a real vault, read-only. On
  978 notes it found three duplicate Arete ids, which is a different defect that
  would have made reconcile-by-tag ambiguous.

## What this cost, and the lesson

The sync path had unit tests over mocks and 2645 lines of integration tests that
have never run, because each waits on a container image that was never
published. Mocked tests agreed with the code about a deck the code never set.

The cheapest test that would have caught all of fault 1, 3 and half of 2 is the
one now in `tests/e2e/test_local_sync.py`. It needs no container, no network and
no running Anki, and it takes a quarter of a second.
