"""How a vault card and an Anki note are recognised as the same card.

The Arete id tag is the identity (`ownership.is_arete_id`). When a note carries no usable
id, the first field is the only other evidence, compared through `first_field_key`.
"""

import re

# Tags Anki itself writes on a note. A sync leaves them on the note, though the vault never
# names them: `leech` is the scheduler's mark on a card it suspended for too many lapses.
ANKI_OWNED_TAGS = frozenset({"leech"})

_HTML_TAG = re.compile(r"<[^>]+>")
_CLOZE_OPEN = re.compile(r"\{\{c\d+::")


def first_field_key(value: str) -> str:
    """A first field reduced for matching by content; an empty key matches nothing.

    HTML tags and cloze markers are dropped, whitespace runs become one space, and the
    result is lower-cased.
    """
    text = _HTML_TAG.sub("", value)
    text = _CLOZE_OPEN.sub("", text).replace("}}", "")
    return " ".join(text.split()).strip().lower()
