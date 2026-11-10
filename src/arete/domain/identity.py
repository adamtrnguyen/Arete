"""How a vault card and an Anki note are recognised as the same card.

The Arete id tag is the identity (`ownership.is_arete_id`). When a note carries no usable
id, the first field is the only other evidence, compared through `first_field_key`.
"""

import re
import unicodedata
from collections.abc import Iterable

# Tags Anki itself writes on a note. A sync leaves them on the note, though the vault never
# names them: `leech` is the scheduler's mark on a card it suspended for too many lapses.
ANKI_OWNED_TAGS = frozenset({"leech"})

_HTML_TAG = re.compile(r"<[^>]+>")
_CLOZE_OPEN = re.compile(r"\{\{c\d+::")
_TAG_SEPARATOR = re.compile("[ 　]")
_ASCII_CONTROL = re.compile("[\x00-\x1f\x7f]")


def anki_tags(tags: Iterable[str]) -> list[str]:
    """The tags Anki keeps for these tag strings, in order, one per case-folded name.

    Anki splits a tag at every space and ideographic space, drops the empty pieces, and in
    each `::` component removes ASCII control characters, trims whitespace, applies NFC,
    and writes an empty component as `blank`: `Cognitive Science` is stored as `Cognitive`
    and `Science`. It does this again on every save that changes the note, and once is not
    always its last word (`:<tab>:` is stored as `::`, and the next edit makes that
    `blank::blank`), so each tag is taken to the form a save leaves unchanged. Send these,
    not the vault's strings: Anki keeps them as sent. A tag already registered in another
    case takes that case, which a comparison ignoring case covers. Pinned against the
    anki library in `tests/infrastructure/anki/test_anki_tags_pin.py`.
    """
    out: dict[str, str] = {}
    for tag in tags:
        for piece in _TAG_SEPARATOR.split(tag):
            if piece:
                name = _tag_name(piece)
                out.setdefault(name.casefold(), name)
    return list(out.values())


def _tag_name(piece: str) -> str:
    name = piece
    for _ in range(8):  # the bound only guards the loop; a property test checks the result
        normal = "::".join(_tag_component(c) for c in name.split("::"))
        if normal == name:
            break
        name = normal
    return name


def _tag_component(component: str) -> str:
    text = _ASCII_CONTROL.sub("", unicodedata.normalize("NFC", component)).strip()
    return text or "blank"


def first_field_key(value: str) -> str:
    """A first field reduced for matching by content; an empty key matches nothing.

    HTML tags and cloze markers are dropped, whitespace runs become one space, and the
    result is lower-cased.
    """
    text = _HTML_TAG.sub("", value)
    text = _CLOZE_OPEN.sub("", text).replace("}}", "")
    return " ".join(text.split()).strip().lower()
