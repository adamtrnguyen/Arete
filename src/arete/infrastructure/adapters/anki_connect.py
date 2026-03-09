import asyncio
import json
import logging
import os
import platform
import re
import shutil
from typing import Any

import httpx

from arete.domain.constants import (
    CHUNK_SIZE,
    FSRS_DIFFICULTY_SCALE,
    REQUEST_TIMEOUT,
    RESPONSIVENESS_TIMEOUT,
    SYNC_CONCURRENCY,
)
from arete.domain.interfaces import AnkiBridge
from arete.domain.models import AnkiCardStats, AnkiDeck, UpdateItem, WorkItem


class AnkiConnectAdapter(AnkiBridge):
    """Adapter for communicating with Anki via the AnkiConnect add-on (HTTP API)."""

    def __init__(self, url: str = "http://127.0.0.1:8765"):
        """Initialize with AnkiConnect URL, auto-detecting WSL bridge if needed."""
        self.logger = logging.getLogger(__name__)
        self._known_decks = set()
        self._model_fields_cache = {}
        self.use_windows_curl = False
        self._client: httpx.AsyncClient | None = None
        self._invoke_sem = asyncio.Semaphore(SYNC_CONCURRENCY)

        # 1. Environment Variable Override (Highest Priority)
        env_host = os.environ.get("ANKI_CONNECT_HOST")
        if env_host:
            # If user provides a host (e.g. 192.168.1.5), we reconstruct the URL
            # Assumes port 8765 if not specified, or user can provide full authority?
            # Let's assume input is just the host IP/name
            url = f"http://{env_host}:8765"
            self.logger.info(f"Using ANKI_CONNECT_HOST override: {url}")
            self.url = url
            return

        # 2. WSL Logic
        if "microsoft" in platform.uname().release.lower():
            # Strategy A: curl.exe bridge (Preferred for 127.0.0.1)
            curl_path = shutil.which("curl.exe")
            if curl_path:
                self.use_windows_curl = True
                if "127.0.0.1" in url or "localhost" in url:
                    url = url.replace("localhost", "127.0.0.1")
                self.logger.info(
                    f"WSL detected: Using curl.exe bridge (found at {curl_path}) to talk to {url}"
                )
                self.url = url
                return
            else:
                self.logger.debug("WSL detected but 'curl.exe' not found in PATH.")

            # Strategy B: /etc/resolv.conf (Fallback)
            if "localhost" in url or "127.0.0.1" in url:
                try:
                    with open("/etc/resolv.conf") as f:
                        for line in f:
                            if line.startswith("nameserver"):
                                host_ip = line.split()[1].strip()
                                url = url.replace("localhost", host_ip).replace(
                                    "127.0.0.1", host_ip
                                )
                                self.logger.info(
                                    f"WSL detected: Auto-corrected URL using resolv.conf to http://{host_ip}:8765"
                                )
                                break
                except Exception as e:
                    self.logger.warning(f"WSL detected but failed to find host IP: {e}")

        self.url = url
        self.logger.debug(
            f"AnkiConnectAdapter initialized with url={self.url} "
            f"(curl_bridge={self.use_windows_curl})"
        )

    @property
    def is_sequential(self) -> bool:
        return False

    async def is_responsive(self) -> bool:
        """Check if AnkiConnect is reachable and has the expected API version."""
        try:
            payload = {"action": "version", "version": 6}
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
            resp = await self._client.post(self.url, json=payload, timeout=RESPONSIVENESS_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return int(data.get("result", 0)) >= 6
            return False
        except Exception:
            return False

    async def get_model_names(self) -> list[str]:
        return await self._invoke("modelNames")

    async def ensure_deck(self, deck: AnkiDeck | str) -> bool:
        name = deck.name if isinstance(deck, AnkiDeck) else deck
        if name in self._known_decks:
            return True
        try:
            await self._invoke("createDeck", deck=name)
            self._known_decks.add(name)
            return True
        except Exception as e:
            self.logger.error(f"Failed to ensure deck '{name}': {e}")
            return False

    async def ensure_model_has_source_field(self, model_name: str) -> bool:
        """Ensure the note model has the _obsidian_source field.

        This enables backwards compatibility for existing cards.
        """
        cache_key = f"_source_field_{model_name}"
        if hasattr(self, cache_key):
            return True

        try:
            # Get current model fields
            fields = await self._invoke("modelFieldNames", modelName=model_name)
            if "_obsidian_source" not in fields:
                # Add the field to the model
                await self._invoke(
                    "modelFieldAdd",
                    modelName=model_name,
                    fieldName="_obsidian_source",
                )
                self.logger.info(f"Added '_obsidian_source' field to model '{model_name}'")

            setattr(self, cache_key, True)
            return True
        except Exception as e:
            self.logger.warning(f"Could not add _obsidian_source field to '{model_name}': {e}")
            return False

    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        # Batch preparation: Ensure decks and models exist once per batch
        unique_decks = {item.note.deck for item in work_items}
        unique_models = {item.note.model for item in work_items}

        for deck_name in unique_decks:
            if not await self.ensure_deck(deck_name):
                # We could fail the whole batch, or just mark them as failed later.
                # For now, failures in ensure_deck will be caught by individual item try-blocks
                # But it's better to log here.
                self.logger.warning(f"Failed to ensure deck '{deck_name}' for batch")

        for model_name in unique_models:
            try:
                await self.ensure_model_has_source_field(model_name)
            except Exception as e:
                self.logger.warning(f"Failed to ensure source field for '{model_name}': {e}")

        sem = asyncio.Semaphore(SYNC_CONCURRENCY)

        async def _bounded(item: WorkItem) -> UpdateItem:
            async with sem:
                return await self._sync_single_note(item)

        return list(await asyncio.gather(*(_bounded(item) for item in work_items)))

    async def _sync_single_note(self, item: WorkItem) -> UpdateItem:
        """Sync a single work item, routing to update or add/heal paths."""
        note = item.note
        try:
            html_fields = dict(note.fields)

            target_nid = None
            info = None
            if note.nid:
                info = await self._invoke("notesInfo", notes=[int(note.nid)])
                if info and info[0].get("noteId"):
                    target_nid = int(note.nid)

            if target_nid:
                return await self._update_existing_note(item, note, html_fields, target_nid, info)
            return await self._add_or_heal_note(item, note, html_fields)

        except Exception as e:
            msg = f"ERR file={item.source_file} card={item.source_index} error={e}"
            self.logger.error(msg)
            return UpdateItem(
                source_file=item.source_file,
                source_index=item.source_index,
                new_nid=None,
                new_cid=None,
                ok=False,
                error=str(e),
                note=note,
            )

    async def _update_existing_note(
        self, item: WorkItem, note: Any, html_fields: dict, target_nid: int, info: Any
    ) -> UpdateItem:
        """Update fields, tags, and deck for an existing note."""
        await self._invoke("updateNoteFields", note={"id": target_nid, "fields": html_fields})

        if info and "tags" in info[0]:
            current_tags = set(info[0]["tags"])
            new_tags = set(note.tags)
            to_add = list(new_tags - current_tags)
            to_remove = list(current_tags - new_tags)
            if to_add:
                await self._invoke("addTags", notes=[target_nid], tags=" ".join(to_add))
            if to_remove:
                await self._invoke("removeTags", notes=[target_nid], tags=" ".join(to_remove))

        if info and "cards" in info[0]:
            await self._invoke("changeDeck", cards=info[0]["cards"], deck=note.deck)
        else:
            self.logger.warning(
                f"[anki] Cannot move cards for nid={target_nid}. Info missing cards: {info}"
            )

        self.logger.debug(f"[update] {item.source_file} #{item.source_index} -> nid={target_nid}")
        return UpdateItem(
            source_file=item.source_file,
            source_index=item.source_index,
            new_nid=str(target_nid),
            new_cid=None,
            ok=True,
            note=note,
        )

    async def _add_or_heal_note(self, item: WorkItem, note: Any, html_fields: dict) -> UpdateItem:
        """Add a new note or heal by matching existing content."""
        existing_nid = await self._find_existing_note(note, html_fields)

        if existing_nid:
            new_id = existing_nid
            await self._invoke("updateNoteFields", note={"id": new_id, "fields": html_fields})
        else:
            params = {
                "note": {
                    "deckName": note.deck,
                    "modelName": note.model,
                    "fields": html_fields,
                    "tags": note.tags,
                    "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                }
            }
            new_id = await self._invoke("addNote", **params)
            if not new_id:
                raise Exception("addNote returned null ID")

        new_cid_val = await self._fetch_cid(new_id)
        await self._populate_nid_field(note, new_id)

        self.logger.info(
            f"[create] {item.source_file} #{item.source_index} -> nid={new_id} cid={new_cid_val}"
        )
        return UpdateItem(
            source_file=item.source_file,
            source_index=item.source_index,
            new_nid=str(new_id),
            new_cid=new_cid_val,
            ok=True,
            note=note,
        )

    @staticmethod
    def _normalize_field(value: str) -> str:
        """Normalize a field value for comparison: strip HTML, cloze markers, whitespace."""
        text = re.sub(r"<[^>]+>", "", value)
        text = re.sub(r"\{\{c\d+::", "", text).replace("}}", "")
        return " ".join(text.split()).strip().lower()

    async def _find_existing_note(self, note: Any, html_fields: dict) -> int | None:
        """Find an existing Anki note by comparing field values directly.

        Queries all notes in the same deck+model, fetches their fields,
        and compares the first field value after normalization.
        """
        first_field_name = next(iter(html_fields))
        our_value = self._normalize_field(html_fields[first_field_name])
        if not our_value:
            return None

        query = f'"deck:{note.deck}" "note:{note.model}"'
        try:
            candidate_nids = await self._invoke("findNotes", query=query)
        except Exception as e:
            self.logger.warning(f"Healing query failed: {e}")
            return None

        if not candidate_nids:
            return None

        # Fetch fields in chunks to avoid overwhelming AnkiConnect
        for i in range(0, len(candidate_nids), CHUNK_SIZE):
            chunk = candidate_nids[i : i + CHUNK_SIZE]
            try:
                infos = await self._invoke("notesInfo", notes=chunk)
            except Exception as e:
                self.logger.warning(f"Healing notesInfo failed: {e}")
                continue

            for info in infos:
                anki_fields = info.get("fields", {})
                anki_val = anki_fields.get(first_field_name, {}).get("value", "")
                if self._normalize_field(anki_val) == our_value:
                    nid = info["noteId"]
                    self.logger.info(f" -> Healed! matched existing note: {nid}")
                    return nid

        return None

    async def _fetch_cid(self, nid: int) -> str | None:
        """Fetch the first card ID for a note."""
        try:
            info_new = await self._invoke("notesInfo", notes=[nid])
            if info_new and info_new[0].get("cards"):
                return str(info_new[0]["cards"][0])
        except Exception as e_cid:
            self.logger.warning(f"Failed to fetch CID for nid={nid}: {e_cid}")
        return None

    async def _populate_nid_field(self, note: Any, nid: int) -> None:
        """Populate the 'nid' field on the Anki note if the model has one."""
        try:
            if note.model not in self._model_fields_cache:
                self._model_fields_cache[note.model] = await self._invoke(
                    "modelFieldNames", modelName=note.model
                )
            if "nid" in self._model_fields_cache[note.model]:
                await self._invoke(
                    "updateNoteFields",
                    note={"id": nid, "fields": {"nid": str(nid)}},
                )
        except Exception as e_field:
            self.logger.warning(f"Failed to populate 'nid' field: {e_field}")

    async def _invoke(self, action: str, **params) -> Any:
        payload = {"action": action, "version": 6, "params": params}
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with self._invoke_sem:
                    data = await asyncio.wait_for(
                        self._http_request(payload), timeout=REQUEST_TIMEOUT
                    )

                if len(data) != 2:
                    raise ValueError("response has an unexpected number of fields")
                if "error" not in data:
                    raise ValueError("response is missing required error field")
                if "result" not in data:
                    raise ValueError("response is missing required result field")
                if data["error"] is not None:
                    raise Exception(data["error"])
                return data["result"]
            except TimeoutError as exc:
                self.logger.warning(
                    f"AnkiConnect timeout on '{action}' (attempt {attempt + 1}/{max_retries + 1})"
                )
                # Reset the HTTP client to drop stale connections
                if self._client is not None:
                    await self._client.aclose()
                    self._client = None
                if attempt < max_retries:
                    await asyncio.sleep(1.0)
                    continue
                raise TimeoutError(
                    f"AnkiConnect timeout on '{action}' after {max_retries + 1} attempts"
                ) from exc
            except Exception as e:
                self.logger.error(f"AnkiConnect call failed: {e}")
                raise

    async def _http_request(self, payload: dict) -> dict:
        if self.use_windows_curl:
            cmd = ["curl.exe", "-s", "-X", "POST", self.url, "-d", "@-"]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin_data = json.dumps(payload).encode("utf-8")
            stdout, stderr = await proc.communicate(input=stdin_data)
            if proc.returncode != 0:
                raise Exception(f"curl.exe failed: {stderr.decode('utf-8')}")
            return json.loads(stdout.decode("utf-8"))
        else:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT,
                    limits=httpx.Limits(
                        max_connections=SYNC_CONCURRENCY,
                        max_keepalive_connections=SYNC_CONCURRENCY,
                    ),
                )
            resp = await self._client.post(self.url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_deck_names(self) -> list[str]:
        return await self._invoke("deckNames")

    async def get_due_cards(
        self, deck_name: str | None = None, include_new: bool = False
    ) -> list[int]:
        """Fetch NIDs of due cards, optionally including new cards."""
        deck_filter = f'deck:"{deck_name}" ' if deck_name else ""
        query = f"{deck_filter}(is:due)" if not include_new else f"{deck_filter}(is:due OR is:new)"

        try:
            return await self._invoke("findNotes", query=query)
        except Exception as e:
            self.logger.error(f"Failed to get due cards: {e}")
            return []

    async def find_all_arete_nids(self) -> list[int]:
        """Find all note IDs that have arete tags."""
        try:
            return await self._invoke("findNotes", query="tag:arete_*")
        except Exception as e:
            self.logger.error(f"Failed to find arete nids: {e}")
            return []

    async def map_nids_to_arete_ids(self, nids: list[int]) -> list[str]:
        """Convert NIDs to Arete IDs via tags."""
        if not nids:
            return []

        arete_ids = []
        try:
            # Chunk request if too large?
            # AnkiConnect default max is usually generous but let's be safe if list is huge.
            # For now assume it fits.
            infos = await self._invoke("notesInfo", notes=nids)
            for note in infos:
                tags = note.get("tags", [])
                for tag in tags:
                    if tag.startswith("arete_"):
                        arete_ids.append(tag)
                        break
        except Exception as e:
            self.logger.error(f"Failed to map nids {nids} to arete ids: {e}")

        return arete_ids

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        # 1. Find notes in deck
        query = f'"deck:{deck_name}"'
        nids = await self._invoke("findNotes", query=query)
        if not nids:
            return {}

        # 2. Get note info to extract 'nid' field
        info = await self._invoke("notesInfo", notes=nids)
        result = {}
        for note in info:
            note_id = note.get("noteId")
            fields = note.get("fields", {})
            nid_val = None
            if "nid" in fields:
                nid_val = fields["nid"]["value"]
                # Strip HTML
                if nid_val.startswith("<p>") and nid_val.endswith("</p>"):
                    nid_val = nid_val[3:-4].strip()

            if nid_val:
                result[nid_val] = note_id
            else:
                self.logger.debug(
                    f"[anki] Note {note_id} has no valid NID. raw_field={fields.get('nid')}"
                )

        self.logger.debug(
            f"[anki] get_notes_in_deck found {len(result)} notes with NIDs in {deck_name}"
        )
        return result

    async def delete_notes(self, nids: list[int]) -> bool:
        self.logger.info(f"Deleting notes: {nids}")
        await self._invoke("deleteNotes", notes=nids)
        return True

    async def delete_decks(self, names: list[str]) -> bool:
        await self._invoke("deleteDecks", decks=names, cardsToo=True)
        return True

    async def get_learning_insights(self, lapse_threshold: int = 3) -> Any:
        from arete.domain.constants import MAX_PROBLEMATIC_NOTES
        from arete.domain.stats.models import LearningStats, NoteInsight

        # Find leech cards directly via AnkiConnect
        leech_cids = await self._invoke("findCards", query=f"prop:lapses>={lapse_threshold}")
        all_cids = await self._invoke("findCards", query="")

        if not leech_cids:
            return LearningStats(total_cards=len(all_cids))

        infos = await self._invoke("cardsInfo", cards=leech_cids)

        # Aggregate by note: keep max lapses per note
        nid_map: dict[int, dict] = {}
        for info in infos:
            nid = info.get("note")
            lapses = info.get("lapses", 0)
            if nid not in nid_map or lapses > nid_map[nid]["lapses"]:
                fields = info.get("fields", {})
                note_name = "Unknown"
                if "_obsidian_source" in fields:
                    note_name = fields["_obsidian_source"].get("value", "Unknown")
                elif fields:
                    first_key = next(iter(fields))
                    note_name = fields[first_key].get("value", "Unknown")
                # Strip HTML
                import re

                note_name = re.sub("<[^<]+?>", "", note_name).strip()

                nid_map[nid] = {
                    "note_name": note_name,
                    "lapses": lapses,
                    "deck": info.get("deckName", "Unknown"),
                }

        problematic = sorted(nid_map.values(), key=lambda x: x["lapses"], reverse=True)
        notes = [
            NoteInsight(
                note_name=n["note_name"],
                issue=f"{n['lapses']} lapses",
                lapses=n["lapses"],
                deck=n["deck"],
            )
            for n in problematic[:MAX_PROBLEMATIC_NOTES]
        ]

        return LearningStats(total_cards=len(all_cids), problematic_notes=notes)

    async def get_card_stats(self, nids: list[int]) -> list[AnkiCardStats]:
        """Fetch stats via AnkiConnect."""
        if not nids:
            return []

        all_stats: list[AnkiCardStats] = []
        for i in range(0, len(nids), CHUNK_SIZE):
            chunk = nids[i : i + CHUNK_SIZE]
            try:
                chunk_stats = await self._fetch_stats_chunk(chunk)
                all_stats.extend(chunk_stats)
            except Exception as e:
                self.logger.error(f"Failed to fetch card stats chunk: {e}")
        return all_stats

    async def _fetch_stats_chunk(self, chunk: list[int]) -> list[AnkiCardStats]:
        """Fetch card stats for a single chunk of NIDs."""
        query = " OR ".join([f"nid:{n}" for n in chunk])
        card_ids = await self._invoke("findCards", query=query)
        if not card_ids:
            return []

        infos = await self._invoke("cardsInfo", cards=card_ids)
        fsrs_map = await self._fetch_fsrs_map(card_ids)

        return [self._build_card_stat(info, fsrs_map) for info in infos]

    async def _fetch_fsrs_map(self, card_ids: list[int]) -> dict[int, float]:
        """Fetch FSRS difficulty scores, returning empty dict on failure."""
        try:
            fsrs_results = await self._invoke("getFSRSStats", cards=card_ids)
            if not fsrs_results or not isinstance(fsrs_results, list):
                return {}
            return {
                item["cardId"]: item["difficulty"] / FSRS_DIFFICULTY_SCALE
                for item in fsrs_results
                if "cardId" in item and "difficulty" in item and item["difficulty"] is not None
            }
        except Exception:
            return {}

    @staticmethod
    def _build_card_stat(info: dict[str, Any], fsrs_map: dict[int, float]) -> AnkiCardStats:
        """Build an AnkiCardStats from a single cardsInfo entry."""
        cid = info.get("cardId", 0)
        difficulty = fsrs_map.get(cid)
        if difficulty is None:
            difficulty = info.get("difficulty")

        front = None
        fields = info.get("fields", {})
        if fields:
            first_key = next(iter(fields))
            front = fields[first_key].get("value")

        return AnkiCardStats(
            card_id=cid,
            note_id=info.get("note", 0),
            lapses=info.get("lapses", 0),
            ease=info.get("factor", 0),
            difficulty=difficulty,
            deck_name=info.get("deckName", "Unknown"),
            interval=info.get("interval", 0),
            due=info.get("due", 0),
            reps=info.get("reps", 0),
            average_time=0,
            front=front,
        )

    async def suspend_cards(self, cids: list[int]) -> bool:
        if not cids:
            return True  # Nothing to do
        res = await self._invoke("suspend", cards=cids)
        return bool(res)

    async def unsuspend_cards(self, cids: list[int]) -> bool:
        if not cids:
            return True
        res = await self._invoke("unsuspend", cards=cids)
        return bool(res)

    async def get_model_styling(self, model_name: str) -> str:
        try:
            res = await self._invoke("modelStyling", modelName=model_name)
            if isinstance(res, dict):
                return res.get("css", "")
            return str(res)
        except Exception:
            return ""

    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        try:
            res = await self._invoke("modelTemplates", modelName=model_name)
            # AnkiConnect returns { "Card 1": { "Front": "...", "Back": "..." } }
            return res
        except Exception:
            return {}

    async def gui_browse(self, query: str) -> bool:
        """Open the Anki browser via AnkiConnect's guiBrowse action."""
        try:
            await self._invoke("guiBrowse", query=query)
            return True
        except Exception as e:
            self.logger.error(f"Failed to open Anki browser: {e}")
            return False

    async def get_card_ids_for_arete_ids(self, arete_ids: list[str]) -> list[int]:
        """Resolve Arete IDs to CIDs, preserving topological input order."""
        if not arete_ids:
            return []
        try:
            cids_ordered: list[int] = []
            seen: set[int] = set()
            CHUNK = 50
            for start in range(0, len(arete_ids), CHUNK):
                chunk = arete_ids[start : start + CHUNK]
                actions = [
                    {"action": "findCards", "params": {"query": f"tag:{aid}"}} for aid in chunk
                ]
                results = await self._invoke("multi", actions=actions)
                for result in results:
                    if isinstance(result, list):
                        for cid in result:
                            if cid not in seen:
                                seen.add(cid)
                                cids_ordered.append(cid)
            return cids_ordered
        except Exception as e:
            self.logger.error(f"Failed to resolve arete IDs: {e}")
            return []

    async def create_topo_deck(
        self, deck_name: str, cids: list[int], reschedule: bool = True
    ) -> bool:
        """Create a filtered deck via the Arete AnkiConnect plugin."""
        if not cids:
            return False
        try:
            await self._invoke(
                "createFilteredDeck",
                name=deck_name,
                cids=cids,
                reschedule=reschedule,
            )
            return True
        except Exception as e:
            self.logger.error(f"create_topo_deck failed: {e}")
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
