from arete.domain.note_types import map_note_type_fields

BASIC = ["Front", "Back", "_obsidian_source"]
CLOZE = ["Text", "Back Extra", "_obsidian_source"]


def test_basic_to_cloze_keeps_question_answer_and_source():
    assert map_note_type_fields(BASIC, CLOZE) == [0, 1, 2]


def test_cloze_to_basic():
    assert map_note_type_fields(CLOZE, BASIC) == [0, 1, 2]


def test_a_same_named_field_wins_over_a_partner():
    old = ["Front", "Back", "Text"]
    assert map_note_type_fields(old, ["Text", "Back Extra"]) == [2, 1]


def test_an_old_field_feeds_one_new_field_only():
    assert map_note_type_fields(["Front"], ["Text", "Front"]) == [-1, 0]


def test_a_field_with_no_source_is_empty():
    assert map_note_type_fields(["Front", "Back"], ["Front", "Back", "Notes"]) == [0, 1, -1]
