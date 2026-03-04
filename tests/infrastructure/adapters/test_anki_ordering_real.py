# Check if anki is available
import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest

from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter

ANKI_AVAILABLE = importlib.util.find_spec("anki") is not None

if ANKI_AVAILABLE:
    from anki.collection import Collection


@pytest.mark.skipif(not ANKI_AVAILABLE, reason="Anki library not installed")
class TestAnkiOrderingReal:
    @pytest.fixture
    def temp_anki_dir(self):
        tmp_dir = tempfile.mkdtemp()
        yield Path(tmp_dir)
        shutil.rmtree(tmp_dir)

    @pytest.fixture
    def setup_anki_data(self, temp_anki_dir):
        """Prepare data and then close the connection so adapter can open it."""
        col_path = temp_anki_dir / "collection.anki2"
        col = Collection(str(col_path))

        # 1. Setup: Create 3 notes/cards
        model = col.models.by_name("Basic")
        deck = col.decks.id("Default")
        model["did"] = deck
        col.models.save(model)

        cids = []
        for i in range(3):
            note = col.new_note(model)
            note.fields[0] = f"Front {i}"
            note.fields[1] = f"Back {i}"
            note.tags = [f"arete_{i}"]
            col.add_note(note, deck)
            cids.append(note.id)
            found = col.find_cards(f"nid:{note.id}")
            cids[i] = found[0]

        # Close explicitly so checking works
        col.close()

        return col_path, cids

    @pytest.fixture
    def adapter(self, temp_anki_dir):
        # We need to ensure AnkiRepository uses our setup.
        # But create_topo_deck instantiates AnkiRepository internally.
        # So we patch the class method or resolution logic.
        from unittest.mock import patch

        # Determine strict path
        col_path = temp_anki_dir / "collection.anki2"

        with patch(
            "arete.infrastructure.anki.repository.AnkiRepository._resolve_collection_path",
            return_value=col_path,
        ):
            yield AnkiDirectAdapter(anki_base=temp_anki_dir)

    @pytest.mark.asyncio
    async def test_create_topo_deck_ordering(self, setup_anki_data, adapter):
        col_path, cids = setup_anki_data

        # 2. Call create_topo_deck (Order: 2, 0, 1)
        target_order_cids = [cids[2], cids[0], cids[1]]

        # Runs successfully because DB is closed
        success = await adapter.create_topo_deck("Target Queue", target_order_cids, reschedule=True)
        assert success is True

        # 3. Re-open to verify
        col = Collection(str(col_path))
        try:
            # Verify Deck Exists and is Dynamic
            did = col.decks.id("Target Queue")
            assert did is not None
            deck_conf = col.decks.get(did)
            assert deck_conf["dyn"] == 1
            assert deck_conf["resched"]

            # Verify Card Placement and Order
            c2 = col.get_card(cids[2])
            c0 = col.get_card(cids[0])
            c1 = col.get_card(cids[1])

            assert c2.did == did
            assert c0.did == did
            assert c1.did == did

            # Verify 'due' values match the loop injection (1000, 1001, 1002)
            assert c2.due == 1000, f"Expected 1000, got {c2.due}"
            assert c0.due == 1001, f"Expected 1001, got {c0.due}"
            assert c1.due == 1002, f"Expected 1002, got {c1.due}"

            print("Verification Successful: Cards are ordered correctly in the filtered deck!")
        finally:
            col.close()

    @pytest.mark.asyncio
    async def test_rebuild_empties_stale_cards(self, setup_anki_data, adapter):
        """Rebuilding a filtered deck should empty stale cards first."""
        col_path, cids = setup_anki_data

        # First build: all 3 cards
        ok = await adapter.create_topo_deck("Queue", cids, reschedule=True)
        assert ok is True

        # Second build: only first 2 cards
        ok = await adapter.create_topo_deck("Queue", cids[:2], reschedule=True)
        assert ok is True

        # Verify: card[2] should NOT be in the filtered deck anymore
        col = Collection(str(col_path))
        try:
            did = col.decks.id("Queue", create=False)
            assert did is not None

            c0 = col.get_card(cids[0])
            c1 = col.get_card(cids[1])
            c2 = col.get_card(cids[2])

            assert c0.did == did
            assert c1.did == did
            assert c2.did != did, "Stale card should have been returned to home deck"

            assert c0.due == 1000
            assert c1.due == 1001
        finally:
            col.close()

    @pytest.mark.asyncio
    async def test_rebuild_reorders_existing_deck(self, setup_anki_data, adapter):
        """Rebuilding with a different order should update due values."""
        col_path, cids = setup_anki_data

        # First build: order [0, 1, 2]
        await adapter.create_topo_deck("Queue", cids)

        # Second build: reversed order [2, 1, 0]
        reversed_cids = list(reversed(cids))
        ok = await adapter.create_topo_deck("Queue", reversed_cids)
        assert ok is True

        col = Collection(str(col_path))
        try:
            did = col.decks.id("Queue", create=False)
            c0 = col.get_card(cids[0])
            c1 = col.get_card(cids[1])
            c2 = col.get_card(cids[2])

            # New order: cids[2] first, cids[1] second, cids[0] third
            assert c2.due == 1000
            assert c1.due == 1001
            assert c0.due == 1002
        finally:
            col.close()
