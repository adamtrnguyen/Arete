from pathlib import Path
from typing import Any, Protocol

import requests

from arete.domain.models import UpdateItem, WorkItem
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from arete.infrastructure.anki.repository import AnkiRepository


class BridgeRunner(Protocol):
    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]: ...

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]: ...

    async def delete_notes(self, nids: list[int]) -> bool: ...

    async def get_card_stats(self, nids: list[int]) -> list[Any]: ...

    async def delete_deck(self, name: str) -> None:
        """Cleanup."""
        ...


class ConnectBridgeRunner:
    def __init__(self, url: str):
        """Initialize ConnectBridgeRunner."""
        self.adapter = AnkiConnectAdapter(url=url)
        self.url = url

    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        return await self.adapter.sync_notes(work_items)

    async def get_note_fields(self, nid: str) -> dict[str, Any]:
        resp = requests.post(
            self.url, json={"action": "notesInfo", "params": {"notes": [int(nid)]}, "version": 6}
        ).json()
        if not resp.get("result"):
            return {}
        return resp["result"][0].get("fields", {})

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        return await self.adapter.get_notes_in_deck(deck_name)

    async def delete_notes(self, nids: list[int]) -> bool:
        return await self.adapter.delete_notes(nids)

    async def get_card_stats(self, nids: list[int]) -> list[Any]:
        return await self.adapter.get_card_stats(nids)

    async def delete_deck(self, name: str) -> None:
        requests.post(
            self.url,
            json={
                "action": "deleteDecks",
                "params": {"decks": [name], "cardsToo": True},
                "version": 6,
            },
        )


class DirectBridgeRunner:
    def __init__(self, anki_base: Path):
        """Initialize DirectBridgeRunner."""
        self.adapter = AnkiDirectAdapter(anki_base=anki_base)
        self.anki_base = anki_base

    async def sync_notes(self, work_items: list[WorkItem]) -> list[UpdateItem]:
        return await self.adapter.sync_notes(work_items)

    async def get_note_fields(self, nid: str) -> dict[str, Any]:
        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return {}
            note = repo.col.get_note(int(nid))
            model = note.note_type()
            fields = {f["name"]: note.fields[i] for i, f in enumerate(model["flds"])}
            # Convert to AnkiConnect-like format for symmetry
            return {k: {"value": v} for k, v in fields.items()}

    async def get_notes_in_deck(self, deck_name: str) -> dict[str, int]:
        return await self.adapter.get_notes_in_deck(deck_name)

    async def delete_notes(self, nids: list[int]) -> bool:
        return await self.adapter.delete_notes(nids)

    async def get_card_stats(self, nids: list[int]) -> list[Any]:
        return await self.adapter.get_card_stats(nids)

    async def delete_deck(self, name: str) -> None:
        await self.adapter.delete_decks([name])
