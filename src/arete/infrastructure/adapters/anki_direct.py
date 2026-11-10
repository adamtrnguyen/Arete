from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from arete.domain.constants import (
    BROWSE_INITIAL_DELAY,
    BROWSE_POLL_ATTEMPTS,
    BROWSE_POLL_INTERVAL,
)
from arete.domain.interfaces import AnkiBridge
from arete.infrastructure.anki.note_session import DirectNoteSession
from arete.infrastructure.anki.repository import AnkiRepository
from arete.infrastructure.anki.search import search_term

if TYPE_CHECKING:  # importing these eagerly cycles inside the anki package
    pass


class AnkiDirectAdapter(AnkiBridge):
    """Direct Python adapter for Anki using the 'anki' library."""

    def __init__(self, anki_base: Path | None):
        """Initialize with path to the Anki base directory."""
        self.anki_base = anki_base
        self.logger = logging.getLogger(__name__)
        self._sync_lock = asyncio.Lock()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[DirectNoteSession]:
        """The collection, open for one batch of note reads and writes (`NoteStorePort`).

        One session at a time: this backend opens the Anki SQLite file itself.
        """
        async with self._sync_lock:
            with AnkiRepository(self.anki_base) as repo:
                if repo.col is None:
                    raise OSError("Anki collection did not open")
                yield DirectNoteSession(repo.col, self.logger)

    async def get_deck_names(self) -> list[str]:
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                # `all_names` prints a deprecation notice to stdout, which a JSON command
                # must keep clean.
                return [d.name for d in repo.col.decks.all_names_and_ids()]
        return []

    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Note ids (not card ids) with a card due today, or new when `include_new`.

        `deck_name` limits the search with Anki's `deck:` filter.
        """
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return []

            deck_filter = f"{search_term('deck', deck_name)} " if deck_name else ""
            query = f"{deck_filter}(is:due OR is:new)" if include_new else f"{deck_filter}(is:due)"

            nids = repo.find_notes(query)
            return nids

    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """The first `arete_` tag of each note in `nids`; a note without one is skipped."""
        arete_ids: list[str] = []

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return []

            for nid in nids:
                try:
                    note = repo.col.get_note(cast(Any, nid))
                    # Check tags for arete_XXX pattern
                    for tag in note.tags:
                        if tag.startswith("arete_"):
                            arete_ids.append(tag)
                            break
                except Exception as e:
                    self.logger.warning(f"Failed to get note {nid}: {e}")

        return arete_ids

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        """Every note in the deck, keyed by its note id as a string."""
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                nids = repo.find_notes(search_term("deck", deck_name))
                return {str(nid): nid for nid in nids}

        return {}

    async def get_note_tags(self, nids: list[int]) -> dict[int, list[str]]:
        """Tags per note. A note that cannot be read is left out, so prune keeps it."""
        tags: dict[int, list[str]] = {}
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return tags
            for nid in nids:
                try:
                    tags[nid] = list(repo.col.get_note(cast(Any, nid)).tags)
                except Exception as e:
                    self.logger.warning(f"Failed to get note {nid}: {e}")
        return tags

    async def delete_notes(self, nids: list[int]) -> bool:
        if not nids:
            return True
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                from anki.notes import NoteId

                repo.col.remove_notes([NoteId(n) for n in nids])
                return True
        return False

    async def delete_decks(self, names: list[str]) -> bool:
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                for name in names:
                    did = repo.col.decks.id(name)
                    if did is not None:
                        repo.col.decks.remove([did])
                return True
        return False

    async def suspend_cards(self, cids: list[int]) -> bool:
        """Suspend cards via Direct DB (queue=-1)."""
        if not cids:
            return True
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return False
            try:
                repo.col.sched.suspend_cards(cast(Any, cids))
                return True
            except Exception as e:
                self.logger.error(f"Failed to suspend cards: {e}")
                return False

    async def unsuspend_cards(self, cids: list[int]) -> bool:
        """Unsuspend cards via Direct DB."""
        if not cids:
            return True
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return False
            try:
                repo.col.sched.unsuspend_cards(cast(Any, cids))
                return True
            except Exception as e:
                self.logger.error(f"Failed to unsuspend cards: {e}")
                return False

    async def get_model_styling(self, model_name: str) -> str:
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return ""
            model = repo.col.models.by_name(model_name)
            if not model:
                return ""
            return model.get("css", "")

    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        """Return template map, e.g. ``{"Card 1": {"Front": ..., "Back": ...}}``."""
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return {}
            model = repo.col.models.by_name(model_name)
            if not model:
                return {}

            result = {}
            for tmpl in model["tmpls"]:
                result[tmpl["name"]] = {"Front": tmpl.get("qfmt", ""), "Back": tmpl.get("afmt", "")}
            return result

    async def gui_browse(self, query: str) -> bool:
        """Open the Anki browser.

        Launch Anki first, wait for startup, then use AnkiConnect to apply the search query.
        """
        import asyncio
        import os
        import subprocess
        import sys
        import urllib.parse

        import httpx

        # Suppress httpx logging for this operation
        logging.getLogger("httpx").setLevel(logging.WARNING)

        async def _try_ankiconnect():
            try:
                async with httpx.AsyncClient() as client:
                    payload = {"action": "guiBrowse", "version": 6, "params": {"query": query}}
                    # Very short timeout for polling
                    resp = await client.post("http://localhost:8765", json=payload, timeout=0.5)
                    if resp.status_code == 200:
                        data = resp.json()
                        return not data.get("error")
            except Exception as e:
                self.logger.debug(f"[direct] AnkiConnect probe failed (Anki closed?): {e}")
            return False

        # 1. ALWAYS launch/bring Anki to front first
        try:
            if sys.platform == "darwin":
                # User requested simple launch
                subprocess.run(
                    ["open", "-a", "Anki"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                uri = f"anki://x-callback-url/search?query={urllib.parse.quote(query)}"
                os.startfile(uri)
            else:
                uri = f"anki://x-callback-url/search?query={urllib.parse.quote(query)}"
                subprocess.run(
                    ["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        except Exception as e:
            self.logger.warning(f"[browse] could not open the Anki browser: {e}")

        await asyncio.sleep(BROWSE_INITIAL_DELAY)

        # 3. Poll AnkiConnect to finish the job (Type into search bar)
        for i in range(BROWSE_POLL_ATTEMPTS):
            if await _try_ankiconnect():
                if i > 0:
                    self.logger.debug(
                        f"AnkiConnect ready after {i * BROWSE_POLL_INTERVAL}s of polling"
                    )
                return True
            await asyncio.sleep(BROWSE_POLL_INTERVAL)

        self.logger.error("Anki launched but search query could not be applied via AnkiConnect.")
        return False

    async def get_card_ids_for_arete_ids(self, arete_ids: list[str]) -> list[int]:
        """Resolve Arete IDs to Anki CIDs using tag search."""
        if not arete_ids:
            return []

        cids_ordered = []
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return []

            # The Arete id is a tag on the note, so `tag:<id>` finds every card of that
            # note. One search per id keeps the input order.

            seen: set[int] = set()
            for aid in arete_ids:
                found = repo.col.find_cards(search_term("tag", aid))
                # Append all cards found (if multiple cards per note, they all go in queue)
                for cid in found:
                    if cid not in seen:
                        seen.add(cid)
                        cids_ordered.append(cid)

        return cids_ordered

    async def create_topo_deck(
        self, deck_name: str, cids: list[int], reschedule: bool = True
    ) -> bool:
        """Create a filtered deck enforcing the order of CIDs provided."""
        if not cids:
            return False

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return False

            # 1. Get/Create Dynamic Deck
            did = repo.col.decks.id(deck_name, create=False)
            if did:
                deck = repo.col.decks.get(did)
                if not deck or not deck.get("dyn"):
                    self.logger.error(f"Deck {deck_name} exists and is not a filtered deck.")
                    return False
                # Empty existing filtered deck before rebuilding
                repo.col.sched.empty_filtered_deck(did)
            else:
                did = repo.col.decks.new_filtered(deck_name)

            # 2. Configure search terms
            deck = repo.col.decks.get(did)
            if not deck:
                return False

            query = " OR ".join([f"cid:{cid}" for cid in cids])
            deck["terms"] = [[query, len(cids), 0]]
            deck["resched"] = reschedule
            repo.col.decks.save(deck)

            # 3. Rebuild (pull cards into filtered deck)
            repo.col.sched.rebuild_filtered_deck(did)

            # 4. Enforce topological order via due values
            for i, cid in enumerate(cids):
                try:
                    card = repo.col.get_card(cast(Any, cid))
                    if card.did == did:
                        card.due = i + 1000
                        repo.col.update_card(card)
                except Exception as e:
                    # Card may not have been pulled into the filtered deck (suspended, etc.)
                    self.logger.debug(f"[queue] could not reorder cid={cid}: {e}")

            return True

    async def close(self) -> None:
        """No long-lived resources to clean up in Direct backend."""
        pass
