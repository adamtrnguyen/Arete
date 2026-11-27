"""Connect Stats Repository — Infrastructure adapter using AnkiConnect HTTP API.

Implements StatsRepository by calling AnkiConnect endpoints.
"""

import json
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
from arete.infrastructure.adapters.anki_connect_client import (
    DEFAULT_ANKI_CONNECT_URL,
    AnkiConnectClient,
)
from arete.infrastructure.adapters.stats.deck_config import deck_params, default_params

logger = logging.getLogger(__name__)


def _optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _parse_fsrs_results(
    fsrs_results: Any,
) -> tuple[dict[int, FsrsMemoryState], dict[int, int]]:
    """Parse additive FSRS-6 fields and fallback `last_review_time` from `getFSRSStats`."""
    fsrs_map: dict[int, FsrsMemoryState] = {}
    last_review_map: dict[int, int] = {}
    if not fsrs_results or not isinstance(fsrs_results, list):
        return fsrs_map, last_review_map

    for item in fsrs_results:
        if not isinstance(item, dict) or item.get("cardId") is None:
            continue
        cid = item["cardId"]
        lrt = item.get("last_review_time")
        if isinstance(lrt, int) and not isinstance(lrt, bool) and lrt > 0:
            last_review_map[cid] = lrt

        # The add-on sends both, or None for both when the card has no FSRS memory state.
        if item.get("difficulty") is None or item.get("stability") is None:
            continue
        fsrs_map[cid] = FsrsMemoryState(
            stability=item["stability"],
            difficulty=item["difficulty"] / FSRS_DIFFICULTY_SCALE,
            retrievability=None,
            decay=_optional_float(item.get("decay")),
            desired_retention=_optional_float(item.get("desired_retention")),
        )
    return fsrs_map, last_review_map


class ConnectStatsRepository(StatsRepository):
    """Fetches card statistics via AnkiConnect HTTP API.

    FSRS state comes from the Arete add-on's getFSRSStats action. Shares
    `AnkiConnectClient` with `AnkiConnectAdapter` for WSL support, connection limits,
    and retry semantics.
    """

    def __init__(
        self,
        url: str = DEFAULT_ANKI_CONNECT_URL,
        *,
        client: AnkiConnectClient | None = None,
    ):
        """Initialize ConnectStatsRepository with a URL or shared transport client."""
        self.client = client or AnkiConnectClient(url=url)

    @property
    def url(self) -> str:
        return self.client.url

    @property
    def _client(self) -> httpx.AsyncClient | None:
        return self.client._client

    @_client.setter
    def _client(self, value: httpx.AsyncClient | None) -> None:
        self.client._client = value

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
            last_review_map: dict[int, int] = {}
            try:
                fsrs_results = await self._invoke("getFSRSStats", cards=card_ids)
                fsrs_map, last_review_map = _parse_fsrs_results(fsrs_results)
            except Exception as e:
                logger.warning(f"[fsrs] getFSRSStats unavailable, stats lack FSRS state: {e}")

            # 3b. Resolve home decks so queued cards (e.g. in Arete::Queue) keep their
            # home deck name and FSRS deck config. Degrades cleanly on stock AnkiConnect.
            home_deck_map = await self._fetch_home_decks(card_ids)

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
                        deck_name=home_deck_map.get(cid) or info.get("deckName", "Unknown"),
                        lapses=info.get("lapses", 0),
                        ease=info.get("factor", 0),
                        interval=info.get("interval", 0),
                        due=info.get("due", 0),
                        reps=info.get("reps", 0),
                        fsrs=fsrs_state,
                        last_review=last_review_map.get(cid),
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
                    s_at_review = None
                    d_at_review = None
                    r_at_review = None
                    raw_data = rev.get("data")
                    if raw_data:
                        try:
                            parsed = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                            if isinstance(parsed, dict):
                                s_at_review = parsed.get("s")
                                d_at_review = parsed.get("d")
                                r_at_review = parsed.get("r")
                        except Exception as e:
                            logger.debug(f"[stats] unreadable revlog data: {e}")
                    entries.append(
                        ReviewEntry(
                            card_id=cid,
                            review_time=rev.get("id", 0) // 1000,
                            rating=rev.get("ease", 0),
                            interval=revlog_interval_days(rev.get("ivl", 0)),
                            last_interval=revlog_interval_days(rev.get("lastIvl", 0)),
                            stability=s_at_review,
                            difficulty=d_at_review,
                            retrievability=r_at_review,
                            time_ms=int(rev.get("time") or 0),
                        )
                    )

        # Sort by review time
        entries.sort(key=lambda r: r.review_time)
        return entries

    async def _fetch_home_decks(self, card_ids: list[int]) -> dict[int, str]:
        """Resolve each card's home deck via `getHomeDecks`, degrading to `{}` on failure."""
        home_deck_map: dict[int, str] = {}
        try:
            home_results = await self._invoke("getHomeDecks", cards=card_ids)
            if isinstance(home_results, list) and len(home_results) == len(card_ids):
                for cid, item in zip(card_ids, home_results, strict=True):
                    if isinstance(item, dict) and item.get("homeDeck"):
                        home_deck_map[cid] = str(item["homeDeck"])
        except Exception as e:
            logger.debug(f"[stats] getHomeDecks unavailable, using cardsInfo deckName: {e}")
        return home_deck_map

    async def _invoke(self, action: str, **params: Any) -> Any:
        """Invoke an AnkiConnect action via the shared transport client."""
        return await self.client.invoke(action, **params)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.close()

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
