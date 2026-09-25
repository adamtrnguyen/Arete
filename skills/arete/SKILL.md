---
name: arete
description: "How a coding agent helps with Arete card work end to end: draft cards into a note, show them live in Obsidian's Card Editor while polishing, sync to Anki, keep the dependency graph healthy, fix cards flagged during review, build study queues, and run vault maintenance. ALWAYS use this skill when creating, editing, syncing, reviewing or repairing Arete cards, when running any `arete` command, or when asked to show a card in Obsidian. For WHAT makes a good card use `card-design`; for HOW to write the YAML read references/FORMAT.md."
---

# Arete — working with the user on cards

Arete syncs cards written in note frontmatter (Obsidian) to Anki. **Obsidian is the source of truth**; Anki is downstream.

| Need | Where |
|---|---|
| Card schema: keys, note types, rules | `references/SCHEMA.md` |
| What to ask, which note type, cue design | a `card-design` skill if installed |
| How to write the YAML so it renders | `references/FORMAT.md` |
| Something looks wrong | `references/TROUBLESHOOTING.md` |

## Ground rules

1. **Never write `id:` or `anki:`.** Arete assigns both on first sync. A new card has neither.
2. **Never pass `--force`, `--backend`, or `--clear-cache` unasked.** Prune always previews and asks.
3. **Sync is the user's call.** It writes to Anki. Draft, show, and polish freely; run `arete sync` when asked.
4. **Cards come from the note body.** Derive fields from what the body says; follow the vault's own writing rules (its CLAUDE.md).
5. **Verify what the user will see.** A check that did not run is not a pass: render, screenshot, or validate.

## Tool map

| Surface | Use it for |
|---|---|
| **Edit/Write the note** | Drafting and polishing cards. The Card Editor updates within ~1 s. |
| `open -g "obsidian://arete?vault=<Vault%20Name>&file=<path>&card=<id or position>"` | Put a card in front of the user without stealing focus. `card` is an Arete id or a 1-based position (drafts have no id). Plugin ≥ 2.5.0. |
| vault MCP `obsidian_screenshot`, `obsidian_dom`, `obsidian_console` | See exactly what the user sees; read render errors. |
| vault MCP `obsidian_click` `.arete-toolbar-btn[title="Preview Mode"]` | Switch the Card Editor to the rendered Anki preview (`.arete-preview-side-btn` toggles Front/Back). |
| `arete` CLI (`uv tool`) | `sync`, `vault check/fix/format`, `graph check/export`, `queue`, `report`, `anki stats`. |
| arete MCP | `list_file_cards`, `get_dep_subgraph`, `check_graph`, `sync_file`, `get_due_cards`, `build_study_queue`. |

## Workflows

### 1. Draft cards for a note

1. **Read the note body** (and `card-design`, if installed). Decide the note type per card.
2. **Write the cards** into the note's `cards:` list following `references/FORMAT.md`. No `id`/`anki`.
3. **Validate:** `arete vault check "<note>.md"` → must pass.
4. **Show card 1:** the `obsidian://arete` link with `card=1`, then Workflow 2.

### 2. Live review loop (the user polishes with you)

1. **Show the card:** `obsidian://arete…&card=<n>`; switch to Preview Mode for the rendered card.
2. **User reacts in the terminal** ("card 3's front is vague").
3. **Edit the field on disk.** The Card Editor re-renders within ~1 s; screenshot to confirm.
4. **Move on:** `card=<n+1>`.

⚠ The Card Editor preview renders through Obsidian, where a single newline is a line break. **Anki does not** (no nl2br). Check line structure against `references/FORMAT.md`, not the preview alone.

### 3. Sync to Anki (when asked)

1. `arete sync "<note>.md" --dry-run` → read what it would send.
2. `arete sync "<note>.md"` → Anki gets the cards; Arete writes `id` and `anki: {nid, cid}` back into the note.
3. **Verify:** the note now carries ids; `arete anki browse` or arete MCP `browse_card` opens it in Anki.

Whole vault: `arete sync --dry-run`, then `arete sync`. Orphans: `arete sync --prune` (previews, asks).

### 4. Dependency graph health

1. `arete graph check` (or `--json`) → cycles, duplicate ids, unresolved refs, unreadable files.
2. **Unresolved ref:** a typo, a note with no cards, or a note that does not exist. Fix the ref; never invent a note to satisfy it.
3. **Ambiguous basename** (two files share a name): use `folder/Name`.
4. **Duplicate id:** two cards share an `id`; keep one (usually the one with deps/anki data).

Wire deps with arete MCP `get_dep_subgraph` for the batch, `requires` for true prerequisites, `related` for peers.

### 5. Fix cards flagged during review

1. `arete report --json` → each flagged card's `file_path`, `line`, `arete_id`, `front`, and the user's `note` (the issue).
2. Open `file_path` at `line`, fix the card, show it (Workflow 2). "No reported cards." means nothing is flagged.
3. After the user confirms: `arete report --clear <n>` (1-based; `0` clears all).

### 6. Study queue

`arete queue --deck "<Deck>" --dry-run` to preview, then without `--dry-run`. `--include-new`, `--depth N`, `--cross-deck`. Output deck: `Arete::Queue`. Sync first.

### 7. Vault maintenance

| Check | Command |
|---|---|
| One file | `arete vault check "<note>.md"`, `arete vault fix "<note>.md"` |
| Every card note | loop `validate_arete_file` from `arete.application.validation` in one Python process (1,168 notes in seconds; the CLI per file is slow) |
| YAML formatting | `arete vault format --dry-run`, then without |
| Graph | `arete graph check` |

A batch rewrite of card fields: go through `parse_frontmatter` → edit values → `rebuild_markdown_with_frontmatter` (`arete.application.utils.text`), and first confirm the no-op round trip is byte-identical per file. Skip any file that is not; never splice lines into YAML by hand. Commit before, diff after, validate every touched file.
