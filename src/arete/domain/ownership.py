"""Which Anki notes Arete owns.

Arete marks every note it creates with the card's Arete id as a tag: `arete_` plus a
26-character ULID (`application/sync/id_service.generate_arete_id`, sent as a tag by
`application/sync/parser`). That tag is the only ownership marker. A note without it was
made by hand or by another tool, and nothing destructive may touch it.

The test is the documented format (`skills/arete/references/SCHEMA.md`: "`arete_` prefix +
26-char ULID"), not the looser `startswith("arete_")` the read paths use: a hand-made tag
such as `arete_review` must never make a note deletable.
"""

import re
from collections.abc import Iterable

# Crockford base32, the ULID alphabet: digits and letters without I, L, O and U.
_ARETE_ID = re.compile(r"arete_[0-9A-HJKMNP-TV-Z]{26}", re.IGNORECASE)


def is_arete_id(tag: str) -> bool:
    """True if `tag` is an Arete card id (`arete_` + 26-char ULID)."""
    return bool(_ARETE_ID.fullmatch(tag))


def id_key(arete_id: str) -> str:
    """What makes two Arete ids one: Anki matches tags ignoring case, so the casefolded id."""
    return arete_id.casefold()


def group_duplicate_ids[T](entries: Iterable[tuple[T, str]]) -> dict[str, list[tuple[T, str]]]:
    """Group `(where, arete_id)` pairs by `id_key`, keeping the ids declared more than once.

    Each entry keeps the id as written.
    """
    grouped: dict[str, list[tuple[T, str]]] = {}
    for where, arete_id in entries:
        grouped.setdefault(id_key(arete_id), []).append((where, arete_id))
    return {key: group for key, group in grouped.items() if len(group) > 1}
