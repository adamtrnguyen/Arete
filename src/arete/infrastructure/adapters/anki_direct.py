from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, cast

from arete.domain.constants import (
    BROWSE_INITIAL_DELAY,
    BROWSE_POLL_ATTEMPTS,
    BROWSE_POLL_INTERVAL,
    FSRS_DIFFICULTY_SCALE,
    MAX_PROBLEMATIC_NOTES,
)
from arete.domain.interfaces import AnkiBridge
from arete.domain.models import AnkiCardStats, AnkiDeck, UpdateItem, WorkItem
from arete.infrastructure.anki.repository import AnkiRepository


class AnkiDirectAdapter(AnkiBridge):
    """Direct Python adapter for Anki using the 'anki' library."""

    def __init__(self, anki_base: Path | None):
        """Initialize with path to the Anki base directory."""
        self.anki_base = anki_base
        self.logger = logging.getLogger(__name__)

    async def get_model_names(self) -> list[str]:
        """Return all model names from the Anki collection."""
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                return [m["name"] for m in repo.col.models.all()]
        return []

    async def ensure_deck(self, deck: AnkiDeck | str) -> bool:
        # AnkiRepository creates/ensures decks on the fly during add_note
        # via 'col.decks.id(name)'.
        # So we can just return True or verify it exists.
        deck_name = deck.name if isinstance(deck, AnkiDeck) else deck
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                # col.decks.id(deck_name) creates it if missing
                repo.col.decks.id(deck_name)
                return True
        return False

    @property
    def is_sequential(self) -> bool:
        return True

    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        results = []

        # Batch operation: Open DB once
        try:
            with AnkiRepository(self.anki_base) as repo:
                for item in work_items:
                    try:
                        self.logger.debug(f"Processing {item.source_file} #{item.source_index}")

                        note_data = item.note
                        success = False
                        new_nid = str(note_data.nid) if note_data.nid else None
                        error_msg = None

                        # 1. Try Update if NID exists
                        if note_data.nid:
                            try:
                                updated = repo.update_note(int(note_data.nid), note_data)
                                if updated:
                                    success = True
                                else:
                                    # Note ID not found in DB? Fallback to Add?
                                    # Ideally we trust the NID. If it's missing, it's deleted.
                                    # Note with ID not found. Will create new note.
                                    self.logger.warning(
                                        f"Note {note_data.nid} not found. Creating new."
                                    )
                                    # Fallthrough to add
                                    pass
                            except Exception as e:
                                error_msg = str(e)
                                self.logger.error(f"Update failed for {note_data.nid}: {e}")

                        # 2. Add if not updated/existing
                        if not success and not error_msg:
                            try:
                                nid_int = repo.add_note(note_data)
                                new_nid = str(nid_int)
                                success = True
                            except Exception as e:
                                error_msg = f"Add failed: {e}"
                                self.logger.error(error_msg)

                        # 3. Compile Result
                        results.append(
                            UpdateItem(
                                source_file=item.source_file,
                                source_index=item.source_index,
                                new_nid=new_nid,
                                new_cid=None,  # CID not easily returned without extra query
                                ok=success,
                                error=error_msg,
                                note=item.note,
                            )
                        )

                    except Exception as e:
                        # Catch-all for item failure
                        results.append(
                            UpdateItem(
                                source_file=item.source_file,
                                source_index=item.source_index,
                                new_nid=None,
                                new_cid=None,
                                ok=False,
                                error=f"Unexpected error: {e}",
                            )
                        )

        except Exception as e:
            # DB Open failure
            self.logger.critical(f"Failed to open Anki DB: {e}")
            # Fail all items
            for item in work_items:
                results.append(
                    UpdateItem(
                        source_file=item.source_file,
                        source_index=item.source_index,
                        new_nid=None,
                        new_cid=None,
                        ok=False,
                        error=f"DB Error: {e}",
                    )
                )

        return results

    async def get_deck_names(self) -> list[str]:
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                return repo.col.decks.all_names()
        return []

    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Get cards due today (and optionally new cards) from Anki.

        Args:
            deck_name: Optional deck name (supports nested, e.g., "Math::Calculus")
            include_new: If True, also include new (unreviewed) cards.

        Returns:
            List of Anki note IDs (nids)

        """
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return []

            deck_filter = f'deck:"{deck_name}" ' if deck_name else ""
            query = (
                f"{deck_filter}(is:due OR is:new)"
                if include_new
                else f"{deck_filter}(is:due)"
            )

            nids = repo.find_notes(query)
            return nids

    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """Map Anki note IDs to Arete card IDs.

        Looks for Arete ID in note tags (arete_XXX pattern).

        Returns:
            List of Arete IDs

        """
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
        # Enable Pruning support!
        with AnkiRepository(self.anki_base) as repo:
            if repo.col:
                # Find direct notes
                # query: "deck:name"
                nids = repo.find_notes(f'"deck:{deck_name}"')
                # We need to map back to obsidian source ID/hash?
                # Arete pruning relies on local state usually, or metadata in fields.
                # AnkiConnect implementation fetches fields.
                # For direct implementation, we can iterate nids and get fields.
                # WARNING: This might be slow for huge decks.
                # For now implementing basic NID list return might not be enough.
                # Interface expects dict {obsidian_id: nid}?
                # Interfaces doc says: "Return mapping of {obsidian_nid: anki_nid}"?
                # Actually PruningService uses this to find what IS in Anki vs what SHOULD be.
                # If we store obsidian ID in a field (e.g. source id), we can map it.
                # Existing logic might rely on content match.

                # Let's verify interface doc or usage.
                # Interface says: "Return mapping of {obsidian_nid: anki_nid}"
                # If Anki notes don't have obsidian_nid stored, this is hard.
                # But typically obsidian_nid IS the anki_nid.
                # So maybe it returns {str(nid): nid}?
                return {str(nid): nid for nid in nids}

        return {}

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

    async def get_learning_insights(self, lapse_threshold: int = 3) -> Any:
        from arete.domain.stats.models import LearningStats, NoteInsight

        total_cards = 0
        problematic_notes = []

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return LearningStats(total_cards=0)

            # 1. Get all card IDs
            cids = repo.col.find_cards("")
            total_cards = len(cids)

            # 2. Iterate cards to find lapses
            # We can use find_notes to get notes with cards having lapses >= threshold
            # But lapses are on cards, not notes.
            # Efficiently find cards with lapses >= threshold
            troublesome_cids = repo.col.find_cards(f"prop:lapses>={lapse_threshold}")

            # Map NIDs to max lapses
            nid_to_lapses = {}
            for cid in troublesome_cids:
                card = repo.col.get_card(cid)
                nid = card.nid
                nid_to_lapses[nid] = max(nid_to_lapses.get(nid, 0), card.lapses)

            # 3. Process notes
            for nid, lapses in nid_to_lapses.items():
                note = repo.col.get_note(nid)
                model = note.note_type()
                if not model:
                    continue

                fields = {f["name"]: note.fields[i] for i, f in enumerate(model["flds"])}

                note_name = "Unknown"
                if "_obsidian_source" in fields:
                    note_name = fields["_obsidian_source"]
                elif fields:
                    note_name = list(fields.values())[0]

                # Strip HTML
                note_name = re.sub("<[^<]+?>", "", note_name).strip()

                problematic_notes.append(
                    NoteInsight(
                        note_name=note_name,
                        issue=f"{lapses} lapses",
                        lapses=lapses,
                        deck=model["name"],
                    )
                )

        # Sort and limit
        problematic_notes.sort(key=lambda x: x.lapses, reverse=True)

        return LearningStats(
            total_cards=total_cards,
            problematic_notes=problematic_notes[:MAX_PROBLEMATIC_NOTES],
        )

    async def get_card_stats(self, nids: list[int]) -> list[AnkiCardStats]:
        """Direct DB implementation of fetching card stats."""
        stats_list = []
        if not nids:
            return []

        # We probably want to chunk this if nids is huge, but start simple.
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return []

            for nid in nids:
                try:
                    # A note can have multiple cards
                    # We need to find cards for this note.
                    # Use col.find_cards(f"nid:{nid}")
                    cids = repo.col.find_cards(f"nid:{nid}")

                    for cid in cids:
                        card = repo.col.get_card(cid)
                        deck = repo.col.decks.get(card.did)
                        deck_name = deck["name"] if deck else "Unknown"

                        # Retrieve difficulty from FSRS memory state if available?
                        # Anki's python library usually exposes FSRS data if v3 scheduler is on?
                        # card.memory_state (v3) might have it.
                        difficulty = None
                        if hasattr(card, "memory_state") and card.memory_state:
                            # FSRS memory state: stability, difficulty, etc.
                            # But access might be opaque. check attributes.
                            # Actually, standard Anki (recent versions) stores custom_data
                            # or memory_state
                            # memory_state.difficulty is 1-10 normally
                            if hasattr(card.memory_state, "difficulty"):
                                difficulty = card.memory_state.difficulty / FSRS_DIFFICULTY_SCALE

                        try:
                            note = repo.col.get_note(card.nid)
                            front = note.fields[0]  # Approximated front
                        except Exception:
                            front = None

                        stats_list.append(
                            AnkiCardStats(
                                card_id=card.id,
                                note_id=card.nid,
                                lapses=card.lapses,
                                ease=card.factor,
                                difficulty=difficulty,
                                deck_name=deck_name,
                                interval=card.ivl,
                                due=card.due,
                                reps=card.reps,
                                average_time=0,  # Not easily available?
                                front=front,
                            )
                        )
                except Exception as e:
                    self.logger.warning(f"Failed to fetch stats for nid={nid}: {e}")

        return stats_list

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
            except Exception:
                pass
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
        except Exception:
            pass

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

            # Efficiently find cards
            # Search query: "tag:arete_ID1 OR tag:arete_ID2 ..."
            # But this is messy if list is huge.
            # Max query length check?
            # Better: find_cards("tag:arete_ID") for each ID? Slow.
            # Best: Iterate IDs and search individually? Or chunk.

            # Actually, `arete_ID` is a tag.
            # So `tag:arete_ID` uniquely identifies cards for that note.
            # A note might have multiple cards. We usually want "Card 1" or all cards.
            # Let's get ALL cards for these tags.

            # BUT topological sort output is `card_id` (Arete ID).
            # In Arete, "card_id" usually maps to a NOTE ID (or specific card if using ID+Type).
            # The `arete_XXX` string is on the Note as a tag.
            # So searching `tag:arete_XXX` gives us CIDs for that note.

            # We must preserve order.

            for aid in arete_ids:
                found = repo.col.find_cards(f"tag:{aid}")
                # Append all cards found (if multiple cards per note, they all go in queue)
                for cid in found:
                    if cid not in cids_ordered:
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
                except Exception:
                    pass  # Card may not have been pulled (suspended, etc.)

            return True

    async def close(self) -> None:
        """No long-lived resources to clean up in Direct backend."""
        pass
