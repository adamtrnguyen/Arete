# AnkiBridge split into role ports (2026-09-08)

Commit `fa84718`. Moved here from the `domain/interfaces.py` docstring on 2026-11-04.

`AnkiBridge` was one interface of 18 methods, so both adapters implemented all 18
whatever they were used for, and a use-case that only reads card statistics still
received a handle that could delete decks. An AST consumer matrix over every call site
showed four clusters that do not overlap:

| Port | Methods |
|---|---|
| `SyncPort` | sync_notes, get_deck_names, get_notes_in_deck, delete_notes, delete_decks |
| `QueuePort` | get_due_cards, map_nids_to_arete_ids, get_card_ids_for_arete_ids, create_topo_deck |
| `CardStatsPort` | get_card_stats, get_learning_insights |
| `AnkiAdminPort` | gui_browse, suspend_cards, unsuspend_cards, get_model_styling, get_model_templates |

Each use-case is typed on its role; `AnkiBridge` remains the composite, the type of an
adapter and of the shared test fake. Mutation check at the time: adding
`await anki_bridge.gui_browse(...)` to the sync pipeline made pyright report
`Cannot access attribute "gui_browse" for class "SyncPort"`.
