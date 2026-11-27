"""Tests for AnkiDirectAdapter against real tmp_path Anki collections.

Every public method of AnkiDirectAdapter is exercised against a real Anki 26
SQLite collection in ``tmp_path``, using minimal typed fakes only for the
defensive ``col is None`` guard, O(1) dedup complexity check, and OS-level
``gui_browse`` launcher/polling.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from anki.collection import Collection
from anki.errors import NotFoundError

from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from tests.e2e.conftest import PROFILE, make_anki_base

# ---------------------------------------------------------------------------
# Real collection helpers & minimal typed fakes
# ---------------------------------------------------------------------------


@contextmanager
def _open_col(base: Path) -> Iterator[Collection]:
    col = Collection(str(base / PROFILE / "collection.anki2"))
    try:
        yield col
    finally:
        col.close()


def _add_note(
    col: Collection,
    *,
    deck: str = "Default",
    front: str = "Q",
    back: str = "A",
    tags: list[str] | None = None,
    model: str = "Basic",
) -> tuple[int, list[int]]:
    did = col.decks.id(deck)
    assert did is not None
    notetype = col.models.by_name(model)
    assert notetype is not None
    note = col.new_note(notetype)
    if model == "Cloze":
        note["Text"] = front
    else:
        note["Front"] = front
        note["Back"] = back
    if tags:
        note.tags = list(tags)
    col.add_note(note, did)
    return int(note.id), [int(c) for c in note.card_ids()]


class _NullCollectionRepo:
    """Typed fake simulating an AnkiRepository whose collection did not open."""

    def __init__(self, _base: Path | None = None):
        """Initialize with `col = None`."""
        self.col = None

    def __enter__(self) -> _NullCollectionRepo:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class _FailingSchedRepo:
    """Typed fake whose scheduler raises on suspend/unsuspend."""

    def __init__(self, _base: Path | None = None):
        """Initialize with a failing scheduler stub."""

        class _Sched:
            def suspend_cards(self, _cids):
                raise RuntimeError("Sched Error")

            def unsuspend_cards(self, _cids):
                raise RuntimeError("Sched Error")

        self.col = SimpleNamespace(sched=_Sched())

    def __enter__(self) -> _FailingSchedRepo:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


@pytest.fixture
def anki_base(tmp_path: Path) -> Path:
    return make_anki_base(tmp_path / "anki")


@pytest.fixture
def adapter(anki_base: Path) -> AnkiDirectAdapter:
    return AnkiDirectAdapter(anki_base=anki_base)


# ---------------------------------------------------------------------------
# get_deck_names / get_filtered_deck_names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_deck_names_and_filtered_deck_names(anki_base: Path, adapter: AnkiDirectAdapter):
    with _open_col(anki_base) as col:
        col.decks.id("Math")
        col.decks.id("Math::Calc")
        col.decks.new_filtered("Arete::Queue")

    names = await adapter.get_deck_names()
    assert set(names) >= {"Default", "Math", "Math::Calc", "Arete::Queue"}

    filtered = await adapter.get_filtered_deck_names()
    assert filtered == {"Arete::Queue"}


# ---------------------------------------------------------------------------
# get_notes_in_deck / get_note_tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_notes_in_deck(anki_base: Path, adapter: AnkiDirectAdapter):
    with _open_col(anki_base) as col:
        nid1, _ = _add_note(col, deck="MyDeck", front="Q1", back="A1")
        nid2, _ = _add_note(col, deck="MyDeck", front="Q2", back="A2")
        _add_note(col, deck="OtherDeck", front="Q3", back="A3")

    res = await adapter.get_notes_in_deck("MyDeck")
    assert res == {str(nid1): nid1, str(nid2): nid2}


@pytest.mark.asyncio
async def test_get_note_tags_skips_missing_note(anki_base: Path, adapter: AnkiDirectAdapter):
    with _open_col(anki_base) as col:
        nid1, _ = _add_note(col, tags=["math", "calculus"])

    tags = await adapter.get_note_tags([nid1, 999_999_999])
    assert tags == {nid1: ["calculus", "math"]}


# ---------------------------------------------------------------------------
# delete_notes / delete_decks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_notes(anki_base: Path, adapter: AnkiDirectAdapter):
    with _open_col(anki_base) as col:
        nid1, _ = _add_note(col, front="Q1")
        nid2, _ = _add_note(col, front="Q2")
        nid3, _ = _add_note(col, front="Q3")

    assert await adapter.delete_notes([]) is True
    assert await adapter.delete_notes([nid1, nid2]) is True

    with _open_col(anki_base) as col:
        remaining = list(col.find_notes(""))
    assert remaining == [nid3]


@pytest.mark.asyncio
async def test_delete_decks_and_skips_missing_without_creating(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    with _open_col(anki_base) as col:
        col.decks.id("OldDeck")

    assert await adapter.delete_decks(["OldDeck"]) is True
    assert await adapter.delete_decks(["MissingDeck"]) is True

    names = await adapter.get_deck_names()
    assert "OldDeck" not in names
    assert "MissingDeck" not in names


# ---------------------------------------------------------------------------
# get_model_styling / get_model_templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_styling_and_templates(anki_base: Path, adapter: AnkiDirectAdapter):
    with _open_col(anki_base) as col:
        basic = col.models.by_name("Basic")
        assert basic is not None
        basic["css"] = ".card { font-size: 20px; }"
        col.models.update_dict(basic)

    css = await adapter.get_model_styling("Basic")
    assert css == ".card { font-size: 20px; }"

    templates = await adapter.get_model_templates("Basic")
    assert "Card 1" in templates
    assert "{{Front}}" in templates["Card 1"]["Front"]
    assert "{{Back}}" in templates["Card 1"]["Back"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,expected",
    [
        pytest.param("get_model_styling", "", id="styling"),
        pytest.param("get_model_templates", {}, id="templates"),
    ],
)
async def test_model_not_found_returns_empty(
    adapter: AnkiDirectAdapter, method: str, expected: object
):
    """When model doesn't exist, return empty default."""
    result = await getattr(adapter, method)("NonExistent")
    assert result == expected


# ---------------------------------------------------------------------------
# suspend_cards / unsuspend_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_and_unsuspend_cards(anki_base: Path, adapter: AnkiDirectAdapter):
    with _open_col(anki_base) as col:
        _, [cid1] = _add_note(col, front="Q1")
        _, [cid2] = _add_note(col, front="Q2")

    assert await adapter.suspend_cards([]) is True
    assert await adapter.suspend_cards([cid1, cid2]) is True
    with _open_col(anki_base) as col:
        assert col.get_card(cid1).queue == -1
        assert col.get_card(cid2).queue == -1

    assert await adapter.unsuspend_cards([]) is True
    assert await adapter.unsuspend_cards([cid1, cid2]) is True
    with _open_col(anki_base) as col:
        assert col.get_card(cid1).queue == 0
        assert col.get_card(cid2).queue == 0


@pytest.mark.asyncio
async def test_suspend_and_unsuspend_cards_error(adapter: AnkiDirectAdapter):
    """When sched.suspend_cards / unsuspend_cards raises, return False."""
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository", _FailingSchedRepo):
        assert await adapter.suspend_cards([1, 2]) is False
        assert await adapter.unsuspend_cards([1, 2]) is False


# ---------------------------------------------------------------------------
# get_due_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_due_cards(anki_base: Path, adapter: AnkiDirectAdapter):
    """Verify due-only, deck-filtered, and include_new queries against real Anki."""
    with _open_col(anki_base) as col:
        due_default, [cid_due_default] = _add_note(col, deck="Default", front="Due default")
        due_math, [cid_due_math] = _add_note(col, deck="Math::Calc", front="Due math")
        new_math, _ = _add_note(col, deck="Math::Calc", front="New math")

        for cid in (cid_due_default, cid_due_math):
            card = col.get_card(cid)
            card.type = 2
            card.queue = 2
            card.ivl = 1
            card.due = 0
            col.update_card(card)

    assert set(await adapter.get_due_cards()) == {due_default, due_math}
    assert await adapter.get_due_cards("Math::Calc") == [due_math]
    assert set(await adapter.get_due_cards("Math::Calc", include_new=True)) == {due_math, new_math}


# ---------------------------------------------------------------------------
# map_nids_to_arete_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_nids_to_arete_ids(anki_base: Path, adapter: AnkiDirectAdapter):
    aid1 = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N1"
    aid2 = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N2"
    with _open_col(anki_base) as col:
        nid1, _ = _add_note(col, front="Q1", tags=["arete_review", aid1, "hard"])
        nid2, _ = _add_note(col, front="Q2", tags=["other", aid2])
        nid3, _ = _add_note(col, front="Q3", tags=["no_id", "arete_short"])

    res = await adapter.map_nids_to_arete_ids([nid1, nid2, nid3])
    assert res == [aid1, aid2]


@pytest.mark.asyncio
async def test_map_nids_to_arete_ids_raises_on_a_note_it_cannot_read(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    """Skipping it would count it as a note without an Arete id, as on AnkiConnect."""
    with _open_col(anki_base) as col:
        nid1, _ = _add_note(col, front="Q1", tags=["arete_01J9Z8Y7X6W5V4T3S2R1Q0P9N1"])

    with pytest.raises(NotFoundError):
        await adapter.map_nids_to_arete_ids([nid1, 999_999_999])


# ---------------------------------------------------------------------------
# get_card_ids_for_arete_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_card_ids_for_arete_ids_order_dedup_and_multi_card(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    aid_a = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9A1"
    aid_b = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9B2"
    aid_c = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9C3"
    aid_cloze = "arete_01J9Z8Y7X6W5V4T3S2R1Q0P9Z9"

    with _open_col(anki_base) as col:
        _, [cid_a] = _add_note(col, front="A", tags=[aid_a])
        _, [cid_b] = _add_note(col, front="B", tags=[aid_b])
        _, [cid_c] = _add_note(col, front="C", tags=[aid_c])
        _, cloze_cids = _add_note(
            col,
            model="Cloze",
            front="{{c1::one}} {{c2::two}} {{c3::three}}",
            tags=[aid_cloze],
        )

    assert await adapter.get_card_ids_for_arete_ids([]) == []
    # Preserves input order
    assert await adapter.get_card_ids_for_arete_ids([aid_c, aid_a, aid_b]) == [cid_c, cid_a, cid_b]
    # Deduplicates repeated IDs while preserving first-seen order
    assert await adapter.get_card_ids_for_arete_ids([aid_a, aid_b, aid_a]) == [cid_a, cid_b]
    # Multi-card Cloze note returns all generated CIDs
    assert len(cloze_cids) == 3
    assert set(await adapter.get_card_ids_for_arete_ids([aid_cloze])) == set(cloze_cids)


@pytest.mark.asyncio
async def test_get_card_ids_for_arete_ids_dedupes_without_rescanning(adapter: AnkiDirectAdapter):
    """Dedup is a set lookup per card, not a scan of the cards found so far."""
    comparisons = 0

    class Cid(int):
        def __eq__(self, other):
            nonlocal comparisons
            comparisons += 1
            return int(self) == int(other)

        __hash__ = int.__hash__

    cids = [Cid(i) for i in range(2000)]
    it = iter(cids)

    class _FindCardsRepo:
        def __init__(self, _base=None):
            self.col = SimpleNamespace(find_cards=lambda _query: [next(it)])

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository", _FindCardsRepo):
        result = await adapter.get_card_ids_for_arete_ids([f"arete_{i}" for i in range(2000)])

    assert result == cids
    assert comparisons < 100  # a list scan makes about 2000 * 1999 / 2


# ---------------------------------------------------------------------------
# create_topo_deck
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_topo_deck_no_collection(adapter: AnkiDirectAdapter):
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository", _NullCollectionRepo):
        assert await adapter.create_topo_deck("Queue", [1, 2, 3]) is False


@pytest.mark.asyncio
async def test_create_topo_deck_creates_reorders_and_empties_existing(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    with _open_col(anki_base) as col:
        home_did = col.decks.id("Math")
        _, [cid1] = _add_note(col, deck="Math", front="Q1")
        _, [cid2] = _add_note(col, deck="Math", front="Q2")

    assert await adapter.create_topo_deck("Arete::Queue", []) is False
    assert await adapter.create_topo_deck("Arete::Queue", [cid2, cid1]) is True

    with _open_col(anki_base) as col:
        fdid = col.decks.id("Arete::Queue", create=False)
        assert fdid is not None
        fdeck = col.decks.get(fdid)
        assert fdeck is not None and fdeck["dyn"] == 1
        assert col.get_card(cid2).did == fdid
        assert col.get_card(cid2).due == 1000
        assert col.get_card(cid1).did == fdid
        assert col.get_card(cid1).due == 1001

    # Rebuilding the existing filtered deck empties old cards back to their home deck.
    assert await adapter.create_topo_deck("Arete::Queue", [cid1]) is True
    with _open_col(anki_base) as col:
        assert col.get_card(cid1).did == fdid
        assert col.get_card(cid1).due == 1000
        assert col.get_card(cid2).did == home_did
        assert col.get_card(cid2).odid == 0


@pytest.mark.asyncio
async def test_create_topo_deck_rejects_non_dynamic_deck(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    with _open_col(anki_base) as col:
        col.decks.id("Arete::Queue")
        _, [cid1] = _add_note(col, deck="Default", front="Q1")

    assert await adapter.create_topo_deck("Arete::Queue", [cid1]) is False


@pytest.mark.asyncio
async def test_create_topo_deck_skips_suspended_and_missing_cards(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    """Suspended cards and missing CIDs do not abort ordering of remaining cards."""
    with _open_col(anki_base) as col:
        home_did = col.decks.id("Default")
        _, [cid1] = _add_note(col, front="Q1")
        _, [cid_suspended] = _add_note(col, front="Q2")
        _, [cid3] = _add_note(col, front="Q3")
        col.sched.suspend_cards([cid_suspended])

    assert await adapter.create_topo_deck("Queue", [cid1, cid_suspended, 999_999_999, cid3]) is True

    with _open_col(anki_base) as col:
        fdid = col.decks.id("Queue", create=False)
        assert fdid is not None
        assert col.get_card(cid1).did == fdid and col.get_card(cid1).due == 1000
        assert col.get_card(cid_suspended).did == home_did
        assert col.get_card(cid3).did == fdid and col.get_card(cid3).due == 1003


@pytest.mark.asyncio
async def test_create_topo_deck_preserves_learning_card_timestamp_due(
    anki_base: Path, adapter: AnkiDirectAdapter
):
    """Learning cards (queue == 1) keep their epoch-timestamp `due` when queued."""
    with _open_col(anki_base) as col:
        _, [cid_learning] = _add_note(col, front="Learning")
        _, [cid_review] = _add_note(col, front="Review")
        # Put cid_learning into learning queue with an epoch timestamp due
        card_l = col.get_card(cid_learning)
        card_l.type = 1
        card_l.queue = 1
        card_l.due = 1_700_000_000
        card_l.left = 1001
        col.update_card(card_l)
        card_r = col.get_card(cid_review)
        card_r.type = 2
        card_r.queue = 2
        card_r.ivl = 5
        card_r.due = 0
        col.update_card(card_r)

    assert await adapter.create_topo_deck("Queue", [cid_learning, cid_review]) is True

    with _open_col(anki_base) as col:
        fdid = col.decks.id("Queue", create=False)
        assert fdid is not None
        c_learn = col.get_card(cid_learning)
        c_rev = col.get_card(cid_review)
        # Learning card stays in queue 1 and is NOT overwritten with positional due 1000
        assert c_learn.did == fdid
        assert c_learn.queue == 1
        assert c_learn.due != 1000
        assert c_rev.did == fdid and c_rev.due == 1001


# ---------------------------------------------------------------------------
# gui_browse (OS launcher + AnkiConnect polling)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gui_browse_timeout(adapter: AnkiDirectAdapter):
    """When AnkiConnect never responds, gui_browse returns False."""
    proc = MagicMock(wait=AsyncMock())
    with (
        patch("sys.platform", "linux"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec,
        patch("httpx.AsyncClient") as mock_client,
        patch("asyncio.sleep", AsyncMock()),
    ):
        mock_client.return_value.__aenter__.return_value.post.side_effect = Exception(
            "No AnkiConnect"
        )
        res = await adapter.gui_browse("query")
    assert res is False
    mock_exec.assert_awaited_once_with(
        "xdg-open",
        "anki://x-callback-url/search?query=query",
        stdout=ANY,
        stderr=ANY,
    )


@pytest.mark.asyncio
async def test_gui_browse_macos(adapter: AnkiDirectAdapter):
    proc = MagicMock(wait=AsyncMock())
    mock_client = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"result": True, "error": None}
    mock_client.post.return_value = mock_resp

    with (
        patch("sys.platform", "darwin"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec,
        patch(
            "httpx.AsyncClient",
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_client)),
        ),
        patch("asyncio.sleep", AsyncMock()),
    ):
        res = await adapter.gui_browse("query")
    assert res is True
    mock_exec.assert_awaited_once_with("open", "-a", "Anki", stdout=ANY, stderr=ANY)


@pytest.mark.asyncio
async def test_gui_browse_windows(adapter: AnkiDirectAdapter):
    mock_client = AsyncMock()
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"result": True, "error": None}
    mock_client.post.return_value = mock_resp

    with (
        patch("sys.platform", "win32"),
        patch("os.startfile", create=True) as mock_start,
        patch(
            "httpx.AsyncClient",
            return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_client)),
        ),
        patch("asyncio.sleep", AsyncMock()),
    ):
        res = await adapter.gui_browse("query")
    assert res is True
    mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# no-collection defensive fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("get_deck_names", [], []),
        ("get_filtered_deck_names", [], set()),
        ("get_due_cards", [], []),
        ("get_notes_in_deck", ["D"], {}),
        ("get_note_tags", [[1]], {}),
        ("delete_notes", [[1]], False),
        ("delete_decks", [["D"]], False),
        ("get_model_styling", ["Basic"], ""),
        ("get_model_templates", ["Basic"], {}),
        ("unsuspend_cards", [[1]], False),
        ("suspend_cards", [[1]], False),
        ("get_card_ids_for_arete_ids", [["arete_1"]], []),
    ],
    ids=[
        "get_deck_names",
        "get_filtered_deck_names",
        "get_due_cards",
        "get_notes_in_deck",
        "get_note_tags",
        "delete_notes",
        "delete_decks",
        "get_model_styling",
        "get_model_templates",
        "unsuspend_cards",
        "suspend_cards",
        "get_card_ids_for_arete_ids",
    ],
)
async def test_no_collection_fallback(
    adapter: AnkiDirectAdapter, method: str, args: list, expected: object
):
    """Parametrized: each method returns its safe default when col is None."""
    with patch("arete.infrastructure.adapters.anki_direct.AnkiRepository", _NullCollectionRepo):
        result = await getattr(adapter, method)(*args)
    assert result == expected
