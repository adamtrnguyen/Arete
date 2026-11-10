r"""Anki search terms built from a name, so the name matches only itself.

In Anki's search syntax `_` matches any one character and `*` any run of characters,
`\` escapes the next character, and `"` ends a quoted term. A deck, note type or tag
name holding one of these, put into a search as it is, matches other names too (`My_Type`
finds `MyXType`) or breaks the search. Every search Arete builds from a name goes
through `search_term`.

What escaping leaves alone, and each caller checks where it matters: Anki compares names
ignoring case, and `deck:` and `tag:` also match the children of the name (`A` finds
`A::B`).
"""

from __future__ import annotations

import re

_SPECIAL = re.compile(r'[\\"*_]')


def escape(text: str) -> str:
    """`text` with Anki's wildcards, its escape character and `"` backslash-escaped."""
    return _SPECIAL.sub(lambda m: "\\" + m.group(0), text)


def search_term(key: str, name: str) -> str:
    """The quoted search `"key:name"` that matches `name` literally (`deck`, `note`, `tag`)."""
    return f'"{key}:{escape(name)}"'
