"""Connect Stats Repository — Infrastructure adapter using AnkiConnect HTTP API.

Implements StatsRepository by calling AnkiConnect endpoints.
"""

import logging
from typing import Any

import httpx

from arete.domain.constants import FSRS_DIFFICULTY_SCALE
from arete.domain.stats.models import (
    CardStatsAggregate,
    FsrsMemoryState,
    ReviewEntry,
    revlog_interval_days,
)
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.adapters.stats.deck_config import deck_params, default_params

logger = logging.getLogger(__name__)


class ConnectStatsRepository(StatsRepository):
    """Fetches card statistics via AnkiConnect HTTP API.

    FSRS state comes from the Arete add-on's getFSRSStats action.
    """

    def __init__(self, url: str = "http://127.0.0.1:8765"):
        """Initialize ConnectStatsRepository."""
        self.url = url
        self._client: httpx.AsyncClient | None = None

    async def get_card_stats(self, nids: list[int]) -> list[CardStatsAggregate]:
        """Fetch comprehensive stats for cards belonging to the given note IDs."""
        if not nids:
            return []

        stats_list: list[CardStatsAggregate] = []
        CHUNK_SIZE = 500

        # A failed read raises: stats with a chunk missing would look complete.
        for i in range(0, len(nids), CHUNK_SIZE):
            chunk = nids[i : i + CHUNK_SIZE]

            # 1. Find cards
            query = " OR ".join([f"nid:{n}" for n in chunk])
            card_ids = await self._invoke("findCards", query=query)
            if not card_ids:
                continue

            # 2. Get card info
            infos = await self._invoke("cardsInfo", cards=card_ids)

            # 3. Try to get FSRS stats (custom action)
            fsrs_map: dict[int, FsrsMemoryState] = {}
            try:
                fsrs_results = await self._invoke("getFSRSStats", cards=card_ids)
                if fsrs_results and isinstance(fsrs_results, list):
                    for item in fsrs_results:
                        # The add-on sends both, or None for both when the card has
                        # no FSRS memory state.
                        if item.get("difficulty") is None or item.get("stability") is None:
                            continue
                        fsrs_map[item["cardId"]] = FsrsMemoryState(
                            stability=item["stability"],
                            difficulty=item["difficulty"] / FSRS_DIFFICULTY_SCALE,
                            retrievability=None,
                        )
            except Exception as e:
                logger.warning(f"[fsrs] getFSRSStats unavailable, stats lack FSRS state: {e}")

            # 4. Build aggregates
            for info in infos:
                cid = info.get("cardId")
                nid = info.get("note")

                fsrs_state = fsrs_map.get(cid)

                # Extract front from fields
                front = None
                fields = info.get("fields", {})
                if fields:
                    first_key = list(fields.keys())[0]
                    front = fields[first_key].get("value")

                stats_list.append(
                    CardStatsAggregate(
                        card_id=cid,
                        note_id=nid,
                        deck_name=info.get("deckName", "Unknown"),
                        lapses=info.get("lapses", 0),
                        ease=info.get("factor", 0),
                        interval=info.get("interval", 0),
                        due=info.get("due", 0),
                        reps=info.get("reps", 0),
                        fsrs=fsrs_state,
                        last_review=None,  # Not available via cardsInfo
                        average_time_ms=0,
                        reviews=[],
                        front=front,
                    )
                )

        return stats_list

    async def get_review_history(self, cids: list[int]) -> list[ReviewEntry]:
        """Fetch review history via AnkiConnect's getReviewsOfCards action.

        A failed read raises: no history would read as cards never reviewed.
        """
        if not cids:
            return []

        entries: list[ReviewEntry] = []

        # getReviewsOfCards returns { cardId: [reviews...] }
        result = await self._invoke("getReviewsOfCards", cards=cids)
        if result and isinstance(result, dict):
            for cid_str, reviews in result.items():
                cid = int(cid_str)
                for rev in reviews:
                    entries.append(
                        ReviewEntry(
                            card_id=cid,
                            review_time=rev.get("id", 0) // 1000,
                            rating=rev.get("ease", 0),
                            interval=revlog_interval_days(rev.get("ivl", 0)),
                            last_interval=revlog_interval_days(rev.get("lastIvl", 0)),
                        )
                    )

        # Sort by review time
        entries.sort(key=lambda r: r.review_time)
        return entries

    async def _invoke(self, action: str, **params: Any) -> Any:
        """Invoke an AnkiConnect action."""
        payload = {"action": action, "version": 6, "params": params}

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)

        response = await self._client.post(self.url, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("error"):
            raise RuntimeError(f"AnkiConnect error: {data['error']}")

        return data.get("result")

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_deck_params(self, deck_names: list[str]) -> dict[str, dict]:
        """Fetch FSRS parameters for the given decks via AnkiConnect.

        Every deck is read in one request (a multi when there are several). A deck Anki
        does not hold gets the defaults; a failed read raises, since the defaults would
        pass for the deck's own settings.
        """
        names = list(dict.fromkeys(deck_names))
        replies: list[Any] = []
        if len(names) == 1:
            replies = [await self._invoke("getDeckConfig", deck=names[0])]
        elif names:
            actions = [
                {"action": "getDeckConfig", "version": 6, "params": {"deck": name}}
                for name in names
            ]
            multi = await self._invoke("multi", actions=actions)
            if not isinstance(multi, list) or len(multi) != len(names):
                raise RuntimeError(f"multi answered {multi!r:.80} for {len(names)} decks")
            for name, reply in zip(names, multi, strict=True):
                error = reply.get("error") if isinstance(reply, dict) else "malformed reply"
                if error:
                    raise RuntimeError(f"AnkiConnect error reading deck {name!r}: {error}")
                replies.append(reply["result"])

        # The add-on answers False for a deck Anki does not hold.
        return {
            deck_name: deck_params(deck_config) if deck_config else default_params()
            for deck_name, deck_config in zip(names, replies, strict=True)
        }
