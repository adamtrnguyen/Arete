"""Field mapping for changing a note's type in place."""

# A field with no same-named counterpart takes its value from its partner, so the
# question and the answer survive Basic <-> Cloze.
FIELD_PARTNERS = {
    "Front": "Text",
    "Text": "Front",
    "Back": "Back Extra",
    "Back Extra": "Back",
}


def map_note_type_fields(old_fields: list[str], new_fields: list[str]) -> list[int]:
    """For each field of the new type, the index of the old field it takes, or -1.

    This is the shape of Anki's ChangeNotetypeRequest.new_fields. An old field feeds
    at most one new field, and a same-named field wins over a partner.
    """
    taken: set[int] = set()
    mapping: list[int] = []
    for name in new_fields:
        idx = old_fields.index(name) if name in old_fields else -1
        mapping.append(idx)
        if idx >= 0:
            taken.add(idx)
    for i, name in enumerate(new_fields):
        partner = FIELD_PARTNERS.get(name)
        if mapping[i] == -1 and partner in old_fields:
            idx = old_fields.index(partner)
            if idx not in taken:
                mapping[i] = idx
                taken.add(idx)
    return mapping
