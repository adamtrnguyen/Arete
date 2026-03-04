"""Healing/recovery tests for AnkiConnectAdapter.

Tests the dict-comparison healing path: when a note has no NID or an invalid NID,
the adapter searches for existing notes by field content and heals the link.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import respx
from httpx import Response

from arete.domain.models import AnkiNote, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


# ---------------------------------------------------------------------------
# Fixtures & helpers (mock-_invoke style)
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter():
    """Adapter with _invoke and ensure_deck mocked for unit-level healing tests."""
    a = AnkiConnectAdapter("http://localhost:8765")
    setattr(a, "_invoke", AsyncMock())  # noqa: B010
    setattr(a, "ensure_deck", AsyncMock(return_value=True))  # noqa: B010
    return a


@pytest.fixture
def adapter_respx():
    """Adapter for respx-level (HTTP) healing tests."""
    return AnkiConnectAdapter(url="http://mock-anki:8765")


def _make_work_item(fields, model="Basic", deck="TestDeck"):
    note = AnkiNote(
        model=model,
        deck=deck,
        fields=fields,
        tags=[],
        start_line=1,
        end_line=5,
        source_file=Path("test.md"),
        source_index=1,
    )
    return WorkItem(note=note, source_file=Path("test.md"), source_index=1)


# ---------------------------------------------------------------------------
# _normalize_field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_field():
    """Verify _normalize_field strips HTML, cloze markers, and normalizes whitespace."""
    norm = AnkiConnectAdapter._normalize_field

    assert norm("Hello World") == "hello world"
    assert norm("<b>Bold</b>") == "bold"
    assert norm("{{c1::answer}} is {{c2::correct}}") == "answer is correct"
    assert norm("<!-- comment -->\n<p>Text</p>") == "text"
    assert norm("  lots   of    spaces  ") == "lots of spaces"
    assert norm("Mixed: {{c1::cloze}} and <em>html</em>") == "mixed: cloze and html"


# ---------------------------------------------------------------------------
# Dict-comparison healing (mock-_invoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healing_via_dict_comparison(adapter):
    """Verify that healing finds an existing note by comparing field values."""
    work_item = _make_work_item({"Front": "What is a claim?", "Back": "Answer"})

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return [100, 200, 300]
        if action == "notesInfo":
            return [
                {"noteId": 100, "fields": {"Front": {"value": "Unrelated"}, "Back": {"value": "X"}}},
                {"noteId": 200, "fields": {"Front": {"value": "What is a claim?"}, "Back": {"value": "Old"}}},
                {"noteId": 300, "fields": {"Front": {"value": "Other"}, "Back": {"value": "Y"}}},
            ]
        if action == "updateNoteFields":
            return None
        if action == "cardsInfo":
            return [{"cardId": 999}]
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "200"


@pytest.mark.asyncio
async def test_healing_cloze_normalization(adapter):
    """Verify that cloze markers are stripped during comparison."""
    work_item = _make_work_item(
        {"Text": "The {{c1::sun}} rises in the {{c2::east}}.", "Back Extra": ""},
        model="Cloze",
    )

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return [500]
        if action == "notesInfo":
            return [
                {
                    "noteId": 500,
                    "fields": {
                        "Text": {"value": "<!-- arete markdown -->\n<p>The {{c1::sun}} rises in the {{c2::east}}.</p>"},
                        "Back Extra": {"value": ""},
                    },
                    "cards": [501],
                }
            ]
        if action == "updateNoteFields":
            return None
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "500"


@pytest.mark.asyncio
async def test_healing_html_normalization(adapter):
    """Verify that HTML tags are stripped during comparison."""
    work_item = _make_work_item({"Front": "<b>Bold</b> question?", "Back": "A"})

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return [700]
        if action == "notesInfo":
            return [
                {
                    "noteId": 700,
                    "fields": {
                        "Front": {"value": "<div><b>Bold</b> question?</div>"},
                        "Back": {"value": "A"},
                    },
                    "cards": [701],
                }
            ]
        if action == "updateNoteFields":
            return None
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is True
    assert results[0].new_nid == "700"


@pytest.mark.asyncio
async def test_healing_no_match_falls_through_to_add(adapter):
    """When no existing note matches, addNote is called."""
    work_item = _make_work_item({"Front": "Brand new card", "Back": "A"})

    call_log = []

    async def side_effect(action, **kwargs):
        call_log.append(action)
        if action == "findNotes":
            return [800]
        if action == "notesInfo":
            if 800 in kwargs.get("notes", []):
                return [
                    {"noteId": 800, "fields": {"Front": {"value": "Different card"}, "Back": {"value": "B"}}}
                ]
            if 900 in kwargs.get("notes", []):
                return [{"noteId": 900, "cards": [901]}]
            return []
        if action == "addNote":
            return 900
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is True
    assert results[0].new_nid == "900"
    assert "addNote" in call_log


@pytest.mark.asyncio
async def test_healing_empty_candidates(adapter):
    """When findNotes returns empty, addNote is called."""
    work_item = _make_work_item({"Front": "Q", "Back": "A"})

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return []
        if action == "addNote":
            return 1001
        if action == "notesInfo":
            return [{"noteId": 1001, "cards": [2002]}]
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is True
    assert results[0].new_nid == "1001"
    assert results[0].new_cid == "2002"


@pytest.mark.asyncio
async def test_healing_query_failure_falls_through(adapter):
    """If findNotes raises, we still try addNote."""
    work_item = _make_work_item({"Front": "Q", "Back": "A"})

    call_log = []

    async def side_effect(action, **kwargs):
        call_log.append(action)
        if action == "findNotes":
            raise Exception("search broken")
        if action == "addNote":
            return 1001
        if action == "notesInfo":
            return [{"noteId": 1001, "cards": [2002]}]
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is True
    assert "addNote" in call_log


@pytest.mark.asyncio
async def test_healing_duplicate_error_propagates_when_no_match(adapter):
    """If addNote says duplicate but healing can't find the note, error propagates."""
    work_item = _make_work_item({"Front": "Q", "Back": "A"})

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return []
        if action == "addNote":
            raise Exception("cannot create note because it is a duplicate")
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is False
    assert "duplicate" in results[0].error


# ---------------------------------------------------------------------------
# CID fetching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cid_fetching_on_create(adapter):
    """Verify that after a successful addNote, we fetch the CID."""
    work_item = _make_work_item({"Front": "Q", "Back": "A"})

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return []
        if action == "addNote":
            return 1001
        if action == "notesInfo":
            if kwargs.get("notes") == [1001]:
                return [{"noteId": 1001, "cards": [2002]}]
            return []
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is True
    assert results[0].new_nid == "1001"
    assert results[0].new_cid == "2002"


@pytest.mark.asyncio
async def test_cid_fetching_on_heal(adapter):
    """Verify that after healing, we also fetch the CID."""
    work_item = _make_work_item({"Front": "DuplicateQ", "Back": "A"})

    async def side_effect(action, **kwargs):
        if action == "findNotes":
            return [5555]
        if action == "notesInfo":
            if kwargs.get("notes") == [5555]:
                return [
                    {
                        "noteId": 5555,
                        "fields": {"Front": {"value": "DuplicateQ"}, "Back": {"value": "old"}},
                        "cards": [6666],
                    }
                ]
            return []
        if action == "updateNoteFields":
            return None
        return None

    adapter._invoke.side_effect = side_effect
    results = await adapter.sync_notes([work_item])

    assert results[0].ok is True
    assert results[0].new_nid == "5555"
    assert results[0].new_cid == "6666"


# ---------------------------------------------------------------------------
# Healing via respx (HTTP-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_healing_failure_respx(adapter_respx):
    """addNote fails with 'duplicate', findNotes returns empty — error propagates."""
    sample_note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Front", "Back": "Back"},
        tags=["tag1"],
        start_line=1,
        end_line=10,
        source_file=Path("test.md"),
        source_index=1,
    )

    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "createDeck":
            return Response(200, json={"result": 1, "error": None})
        if action == "addNote":
            return Response(
                200, json={"result": None, "error": "cannot create note because it is a duplicate"}
            )
        if action == "findNotes":
            return Response(200, json={"result": [], "error": None})
        return Response(200, json={"result": None, "error": None})

    respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    item = WorkItem(note=sample_note, source_file=Path("test.md"), source_index=1)
    results = await adapter_respx.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is False
    assert "duplicate" in results[0].error


@pytest.mark.asyncio
@respx.mock
async def test_healing_success_respx(adapter_respx):
    """Dict-comparison healing via HTTP: findNotes returns candidates, match is found."""
    sample_note = AnkiNote(
        model="Basic",
        deck="Default",
        fields={"Front": "Front", "Back": "Back"},
        tags=["tag1"],
        start_line=1,
        end_line=10,
        source_file=Path("test.md"),
        source_index=1,
    )

    def side_effect(request):
        data = json.loads(request.content)
        action = data["action"]
        if action == "createDeck":
            return Response(200, json={"result": 1, "error": None})
        if action == "findNotes":
            return Response(200, json={"result": [123999], "error": None})
        if action == "notesInfo":
            notes = data.get("params", {}).get("notes", [])
            if 123999 in notes:
                return Response(200, json={"result": [
                    {
                        "noteId": 123999,
                        "fields": {"Front": {"value": "Front"}, "Back": {"value": "Back"}},
                        "cards": [999],
                    }
                ], "error": None})
            return Response(200, json={"result": [], "error": None})
        if action == "updateNoteFields":
            return Response(200, json={"result": None, "error": None})
        return Response(200, json={"result": None, "error": None})

    respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    item = WorkItem(note=sample_note, source_file=Path("test.md"), source_index=1)
    results = await adapter_respx.sync_notes([item])

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].new_nid == "123999"
