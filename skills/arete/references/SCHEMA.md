# Arete card schema

What Arete reads from a note's frontmatter. Style (how to write it so it renders) is in `FORMAT.md`.

## File-Level Frontmatter

A file is an Arete note when its frontmatter has `arete: true` and a non-empty `cards` list.

```yaml
---
arete: true                        # Required marker
deck: "Parent::Child"              # Anki deck, :: for nesting. Required at file or card level.
model: "Basic"                     # Default note type. Options: Basic, Cloze, or custom model name.
tags: [tag1, tag2]                 # Applied to all cards in file
cards:
  - ...                            # Required, non-empty list
---
```

## Card Schema — Basic Model

```yaml
cards:
  - id: arete_01KFA531FYFDAHH1RXBXGE3ZNX   # Auto-generated ULID on first sync if missing
    Front: "Question text"                   # Required, non-empty
    Back: |-                                 # Required, non-empty. Use |- for multiline.
      Answer text
      with multiple lines
    deck: "Override::Deck"                   # Optional, overrides file-level deck
    model: "Basic"                           # Optional, overrides file-level model
    tags: [extra-tag]                        # Optional, overrides file-level tags
    deps:
      requires: [arete_ULID, basename]       # Prerequisites (card IDs or filenames without .md)
      related: [arete_ULID]                  # Related cards (informational, not traversed)
    anki:                                    # Written by Arete after sync — do not manually set
      nid: "1767401291489"
      cid: "1767583698928"
```

## Card Schema — Cloze Model

```yaml
cards:
  - id: arete_01KFA531FYFDAHH1RXBXGE3ZN9
    model: Cloze
    Text: "The {{c1::epidermis}} is the {{c2::outermost}} layer of skin."   # Required
    "Back Extra": "Additional context"                                       # Optional
```

## Card Schema — Custom Models

Any field name not in the reserved set becomes an Anki field. At least one field required.
Reserved keys: `model, deck, tags, id, deps, anki`

## Key Rules

- **IDs**: `arete_` prefix + 26-char ULID. Auto-generated on first sync. Do not fabricate.
- **anki.nid / anki.cid**: Written by Arete after sync. Never manually create or modify these.
- **Deck**: Must be specified at file level or card level. Use `::` for nesting.
- **Multiline content**: Use YAML block scalar `|-` (literal, strip trailing newline).
- **Math**: `$...$` and `$$...$$` are auto-converted to `\(...\)` and `\[...\]`.
- **Images**: `![[image.png]]` wikilinks and `![](image.png)` are auto-resolved and copied to Anki media.
- **No duplicate YAML keys**: Arete uses a strict YAML loader that rejects duplicates.
- **No tabs in YAML**: Use spaces only.
- **Deps references**: an `arete_ULID` (one card), a basename like `algebra` (all cards in `algebra.md`), or `folder/Name` when several files share a basename.


## Complete Example

```yaml
---
arete: true
deck: "Biology::Human Anatomy"
model: Basic
tags: [anatomy, high-retention]
cards:
  - id: arete_01KFA531FYFDAHH1RXBXGE3ZNX
    Front: What are the three layers of the skin?
    Back: |-
      1. Epidermis (outer)
      2. Dermis (middle, contains collagen)
      3. Hypodermis (inner, fat layer)
    deps:
      requires: []
      related: [arete_01KFA531FYFDAHH1RXBXGE3ZN9]

  - id: arete_01KFA531FYFDAHH1RXBXGE3ZN9
    model: Cloze
    Text: The {{c1::epidermis}} is the {{c2::outermost}} layer of skin.
    "Back Extra": Contains melanocytes for pigmentation
---
```

