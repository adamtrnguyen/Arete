"""AnkiConnectAdapter calls behind the stats dashboard, suspend/unsuspend and the queue."""

import logging

import pytest

from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

from .fake_anki import FakeAnki


class Fake(FakeAnki):
    """FakeAnki plus the actions these calls use, driven by plain dicts."""

    def __init__(self):
        """Start empty: no cards, nothing suspended, nothing browsed."""
        super().__init__()
        self.find_cards: dict[str, list[int]] = {}
        self.cards_info: list[dict] = []
        self.suspended: list[int] = []
        self.browsed: list[str] = []
        self.filtered: list[dict] = []

    def _a_findCards(self, query):
        return list(self.find_cards.get(query, []))

    def _a_cardsInfo(self, cards):
        return [c for c in self.cards_info if c["cardId"] in cards]

    def _a_suspend(self, cards):
        self.suspended.extend(cards)
        return True

    def _a_unsuspend(self, cards):
        self.suspended = [c for c in self.suspended if c not in cards]
        return True

    def _a_guiBrowse(self, query):
        self.browsed.append(query)
        return [1]

    def _a_createFilteredDeck(self, **kwargs):
        self.filtered.append(kwargs)


def _adapter(fake: FakeAnki) -> AnkiConnectAdapter:
    adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
    adapter._http_request = fake.http  # type: ignore[method-assign]
    return adapter


# --- suspend / unsuspend ---


@pytest.mark.asyncio
async def test_suspending_nothing_sends_nothing():
    fake = Fake()
    assert await _adapter(fake).suspend_cards([]) is True
    assert await _adapter(fake).unsuspend_cards([]) is True
    assert not fake.requests


@pytest.mark.asyncio
async def test_suspend_then_unsuspend_reach_the_cards_named():
    fake = Fake()
    adapter = _adapter(fake)
    assert await adapter.suspend_cards([5, 6]) is True
    assert fake.suspended == [5, 6]
    assert await adapter.unsuspend_cards([5]) is True
    assert fake.suspended == [6]


# --- browse ---


@pytest.mark.asyncio
async def test_browse_sends_the_query_and_reports_success():
    fake = Fake()
    assert await _adapter(fake).gui_browse("deck:Math") is True
    assert fake.browsed == ["deck:Math"]


@pytest.mark.asyncio
async def test_browse_reports_failure_instead_of_raising(caplog):
    fake = Fake()
    fake.fail = lambda action, _p: "no gui" if action == "guiBrowse" else None
    with caplog.at_level(logging.ERROR):
        assert await _adapter(fake).gui_browse("deck:Math") is False
    assert "Failed to open Anki browser: no gui" in caplog.text


# --- resolving Arete ids to card ids ---


@pytest.mark.asyncio
async def test_card_ids_keep_the_input_order_and_drop_repeats():
    fake = Fake()
    fake.find_cards = {"tag:arete_b": [20, 21], "tag:arete_a": [10, 21], "tag:arete_c": []}
    cids = await _adapter(fake).get_card_ids_for_arete_ids(["arete_b", "arete_a", "arete_c"])
    assert cids == [20, 21, 10]


@pytest.mark.asyncio
async def test_card_ids_for_no_ids_ask_anki_nothing():
    fake = Fake()
    assert await _adapter(fake).get_card_ids_for_arete_ids([]) == []
    assert not fake.requests


@pytest.mark.asyncio
async def test_card_ids_are_looked_up_fifty_ids_per_request():
    fake = Fake()
    ids = [f"arete_{i:03d}" for i in range(120)]
    fake.find_cards = {f"tag:{a}": [i] for i, a in enumerate(ids)}
    cids = await _adapter(fake).get_card_ids_for_arete_ids(ids)
    assert cids == list(range(120))
    assert fake.requests["multi"] == 3


@pytest.mark.asyncio
async def test_card_ids_resolve_to_nothing_when_every_lookup_errors():
    fake = Fake()
    fake.fail = lambda action, _p: "locked" if action == "findCards" else None
    assert await _adapter(fake).get_card_ids_for_arete_ids(["arete_a", "arete_b"]) == []


@pytest.mark.asyncio
async def test_card_ids_resolve_to_nothing_when_the_multi_request_itself_fails(caplog):
    fake = Fake()
    fake.fail = lambda action, _p: "locked" if action == "multi" else None
    with caplog.at_level(logging.ERROR):
        assert await _adapter(fake).get_card_ids_for_arete_ids(["arete_a"]) == []
    assert "Failed to resolve arete IDs: locked" in caplog.text


# --- filtered deck ---


@pytest.mark.asyncio
async def test_a_filtered_deck_of_no_cards_is_not_created():
    fake = Fake()
    assert await _adapter(fake).create_topo_deck("Arete::Queue", []) is False
    assert not fake.requests


@pytest.mark.asyncio
async def test_a_filtered_deck_is_created_with_the_cards_in_order():
    fake = Fake()
    ok = await _adapter(fake).create_topo_deck("Arete::Queue", [3, 1, 2], reschedule=False)
    assert ok is True
    assert fake.filtered == [{"name": "Arete::Queue", "cids": [3, 1, 2], "reschedule": False}]


@pytest.mark.asyncio
async def test_a_refused_filtered_deck_reports_false(caplog):
    fake = Fake()
    fake.fail = lambda action, _p: "deck exists" if action == "createFilteredDeck" else None
    with caplog.at_level(logging.ERROR):
        assert await _adapter(fake).create_topo_deck("Arete::Queue", [1]) is False
    assert "create_topo_deck failed: deck exists" in caplog.text
