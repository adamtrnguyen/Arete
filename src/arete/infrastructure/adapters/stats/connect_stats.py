"""Connect Stats Repository — Infrastructure adapter using AnkiConnect HTTP API.

Implements StatsRepository by calling AnkiConnect endpoints.
"""

import logging
from typing import Any

import httpx

from arete.domain.stats.models import CardStatsAggregate, FsrsMemoryState, ReviewEntry
from arete.domain.stats.ports import StatsRepository

logger = logging.getLogger(__name__)


class ConnectStatsRepository(StatsRepository):
    """Fetches card statistics via AnkiConnect HTTP API.

    Attempts custom getFSRSStats action if available, otherwise falls back
    to standard cardsInfo.
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

        for i in range(0, len(nids), CHUNK_SIZE):
            chunk = nids[i : i + CHUNK_SIZE]

            try:
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
                            if all(k in item for k in ["cardId", "difficulty", "stability"]):
                                fsrs_map[item["cardId"]] = FsrsMemoryState(
                                    stability=item.get("stability", 0),
                                    difficulty=item.get("difficulty", 0) / 10.0,
                                    retrievability=item.get("retrievability"),
                                )
                except Exception:
                    # getFSRSStats not available
                    pass

                # 4. Build aggregates
                for info in infos:
                    cid = info.get("cardId")
                    nid = info.get("note")

                    fsrs_state = fsrs_map.get(cid)

                    # Fallback: Try difficulty from cardsInfo if FSRS not available
                    if not fsrs_state and info.get("difficulty") is not None:
                        fsrs_state = FsrsMemoryState(
                            stability=0,  # Unknown
                            difficulty=info["difficulty"] / 10.0,
                            retrievability=None,
                        )

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

            except Exception as e:
                logger.error(f"Failed to fetch card stats chunk: {e}")

        return stats_list

    async def get_review_history(self, cids: list[int]) -> list[ReviewEntry]:
        """Fetch review history via AnkiConnect's getReviewsOfCards action."""
        if not cids:
            return []

        entries: list[ReviewEntry] = []

        try:
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
                                interval=rev.get("ivl", 0),
                                last_interval=rev.get("lastIvl", 0),
                                time_taken=rev.get("time", 0),
                                review_type=rev.get("type", 0),
                            )
                        )
        except Exception as e:
            logger.warning(f"Failed to fetch review history: {e}")

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

        Falls back to defaults if the custom action isn't available.
        """
        params: dict[str, dict] = {}

        for deck_name in deck_names:
            try:
                # Try to get deck config via AnkiConnect
                deck_config = await self._invoke("getDeckConfig", deck=deck_name)
                if deck_config:
                    fsrs = deck_config.get("fsrs", {})
                    default_retention = fsrs.get("desiredRetention", 0.9)
                    params[deck_name] = {
                        "desired_retention": deck_config.get("desiredRetention", default_retention),
                        "weights": fsrs.get("w", []),
                        "sm2_retention": deck_config.get("sm2Retention", 0.9),
                    }
                else:
                    # Default values
                    params[deck_name] = {
                        "desired_retention": 0.9,
                        "weights": [],
                        "sm2_retention": 0.9,
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch deck params for {deck_name}: {e}")
                # Provide defaults on failure
                params[deck_name] = {
                    "desired_retention": 0.9,
                    "weights": [],
                    "sm2_retention": 0.9,
                }

        return params
