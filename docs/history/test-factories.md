# Why the tests build cards through factories

Moved here from the `tests/factories.py` and `obsidian-plugin/tests/factories.ts` docstrings.
The factories keep the rule; this keeps the measurement that produced it.

## Hand-written cards that tested nothing

Before `tests/factories.py`, every test hand-wrote its data: 28 `AnkiNote(...)` literals
across 8 files, raw card YAML in 23 files, and the app config built 8 different ways.

The cost showed up in the Obsidian plugin. Three test files each hand-wrote a card with a
flat `nid:` key. Arete writes `anki: {nid: ...}` nested. The three files agreed with each
other and disagreed with the product, so they passed while testing nothing. The same files
carried FSRS difficulty on Anki's 1..10 scale when the backend sends 0..1, which is why
every difficulty threshold in the interface was dead.

`tests/test_factories.py` now feeds the factory output through Arete's own parser and
fails when the two disagree.
