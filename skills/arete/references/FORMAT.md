# Arete card YAML — format guide

How to write `cards:` so a card renders the same in Obsidian and in Anki. Schema lives in
`SCHEMA.md`; this file is the **style**. Rules marked 📏 were measured against
arete's converter on 2026-09-25.

## The shape

```yaml
cards:
  - Front: |-
      What does FlashAttention tile into SRAM?
    Back: |-
      Blocks of $Q$, $K$ and $V$, sized so each fits in on-chip SRAM.

      This keeps the $N \times N$ score matrix out of HBM.
    Back Extra: |-
      (Dao et al., *FlashAttention*, §3.1)
    deps:
      requires: [Self-Attention]
      related: []
```

| Rule | Why |
|---|---|
| **Question field first** (`Front`, or `Text` for Cloze), then answer fields, then `deps` | Scans top-down like the card. |
| **`id` and `anki` last, and only Arete writes them** | Arete appends them on first sync. |
| **`Back Extra` holds citations and asides**, not the answer | Anki shows it under the answer, smaller. |
| **Multi-line value → `\|-` block** | Keeps backslashes and quotes literal. |

## Line breaks 📏

arete's converter runs Python-Markdown with `fenced_code` and `tables` only — **no nl2br**.

| You write | Anki shows |
|---|---|
| two lines separated by **one** newline | **one line** (joined with a space) |
| a **blank line** between them | two paragraphs |
| `- item` right after a text line | plain text, **not a list** |
| a blank line, then `- item` lines | a list |
| a table right after a text line | plain text, **not a table** |
| a ` ``` ` fence right after a text line | a code block (fences do not need the blank line) |

⚠ The Obsidian Card Editor preview treats one newline as a break, so it can look right
while Anki joins the lines. **Use blank lines.** Never end lines with two spaces for a hard
break: a rewrite by arete's YAML writer turns such a value into one double-quoted line.

## Backslashes and escaping

In a `|-` block **every character is literal**. Write LaTeX exactly as in a note.

| Right (`\|-` block) | Wrong | Renders as |
|---|---|---|
| `$\alpha \in \mathbb{Q}$` | `$\\alpha \\in \\mathbb{Q}$` | a line break (`\\`) then the letters "alpha" |
| a real line break | `\n` typed as two characters | the literal text `\n` |
| `"quoted"` inside code | `\"quoted\"` | backslashes in the card |

Double escaping is what an import produces when it writes YAML by string-pasting (one
real vault had 19 notes with it, up to 8 backslashes deep). In a double-quoted scalar (`"..."`) YAML
does interpret `\\` and `\n` — another reason to prefer `|-`.

## Math

| Rule | Detail |
|---|---|
| Inline `$...$`, display `$$...$$` | Converted to `\(...\)` and `\[...\]` for Anki. |
| Display math: `$$` on its own lines | Keep the formula lines between them. |
| `$\sim$100x` is math | Obsidian and Arete both read it as math, so `$` needs a closing pair. |
| State shapes on first use | e.g. `x ∈ ℝᵈ`, not a bare `x ∈ ℝ` for a vector. |

## Cloze

```yaml
  - model: Cloze
    Text: |-
      FlashAttention uses the {{c1::online softmax}} trick with a running {{c2::max and sum::two statistics}}.
    Back Extra: |-
      (Dao et al., §3.1)
```

- **`{{cN::answer}}` or `{{cN::answer::hint}}`.** Same `N` = hidden together on one card.
- **A deletion ends at the first `}}`.** 📏 `{{c1::$e^{x^{2}}$}}` is cut to `$e^{x^{2`; write `{{c1::$e^{x^{2} }$}}` (space between the braces). Anki's own parser has the same rule (recalled, not measured).
- The Card Editor preview shows the lowest `cN`.

## Dependencies

| Form | Resolves to |
|---|---|
| `arete_<ULID>` | that one card |
| `Note Name` | every card in the one file with that name |
| `folder/Note Name` | picks one when several files share the name |
| a single value (`requires: Algebra`) | allowed; a list is clearer |

`requires` = must know first (walked by the queue). `related` = peers (not walked).
A ref to a note with no cards, or to a missing note, is reported by `arete graph check`.

## Never in card fields

- **Footnote markers `[^id]`** — Anki has no footnotes; cite inline, e.g. `(Author, p. 12)`.
- **`id:` / `anki:` you made up** — Arete issues both.
- **Tabs** — YAML forbids them; use spaces.
- **Duplicate keys** — Arete's loader rejects the file.

## Check before handing back

1. `arete vault check "<note>.md"` passes.
2. No `\\` before a letter, no literal `\n`, no line ending in spaces.
3. A blank line before every list and table, and between separate paragraphs.
4. Show the card in the Card Editor preview (SKILL.md, Workflow 2).
