"""Direct Stats Repository — Infrastructure adapter for Anki's SQLite database.

Implements StatsRepository by querying the Anki collection directly.
"""

import logging
from pathlib import Path
from typing import Any, cast

from arete.domain.constants import FSRS_DIFFICULTY_SCALE
from arete.domain.stats.models import (
    CardStatsAggregate,
    FsrsMemoryState,
    ReviewEntry,
    revlog_interval_days,
)
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.adapters.stats.deck_config import deck_params, default_params
from arete.infrastructure.anki.repository import AnkiRepository

logger = logging.getLogger(__name__)


def _optional_float(value: Any) -> float | None:
    """Coerce a real number to float, ignoring None, booleans, and mock placeholders."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_positive_int(value: Any) -> int | None:
    """Return a positive integer timestamp, ignoring None, booleans, and non-ints."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def fsrs_state_of(card: Any) -> FsrsMemoryState | None:
    """Return the card's FSRS state in 0-1 difficulty units, or None if unreviewed under FSRS."""
    ms = card.memory_state  # Optional[FsrsMemoryState protobuf]
    if not ms:
        return None
    return FsrsMemoryState(
        stability=ms.stability,
        difficulty=ms.difficulty / FSRS_DIFFICULTY_SCALE,
        retrievability=None,  # computed by the application layer
        decay=_optional_float(getattr(card, "decay", None)),
        desired_retention=_optional_float(getattr(card, "desired_retention", None)),
    )


def _as_int(nid: Any) -> int | None:
    """The note id as a number, or None for a value that is not one."""
    try:
        return int(nid)
    except (TypeError, ValueError):
        return None


_SQL_CHUNK = 500  # ids per IN (...) query, well under SQLite's variable limit


class DirectStatsRepository(StatsRepository):
    """Fetches card statistics directly from Anki's SQLite database.

    Accesses FSRS memory state via card.memory_state when available.
    """

    def __init__(self, anki_base: Path | None = None):
        """Initialize DirectStatsRepository."""
        self.anki_base = anki_base

    async def get_card_stats(self, nids: list[int]) -> list[CardStatsAggregate]:
        """Fetch comprehensive stats for cards belonging to the given note IDs.

        A failed read raises: stats with notes missing would look complete. A note id that
        is not a number, or names no note, has no cards and no stats.
        """
        if not nids:
            return []

        stats_list: list[CardStatsAggregate] = []

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                raise OSError("Anki collection did not open")

            # Grouped reads: the cards of every note and the revlog aggregates of every
            # card, one query per chunk; deck and note lookups once per deck and note.
            cids_of = self._cids_by_note(repo, nids)
            revlog = self._revlog_aggregates(repo, [c for cids in cids_of.values() for c in cids])
            deck_names: dict[int, str] = {}
            fronts: dict[int, str | None] = {}

            for nid in nids:
                key = _as_int(nid)
                for cid in cids_of.get(key, []) if key is not None else []:
                    card = repo.col.get_card(cast(Any, cid))
                    if card.did not in deck_names:
                        deck = repo.col.decks.get(card.did)
                        deck_names[card.did] = deck["name"] if deck else "Unknown"
                    deck_name = deck_names[card.did]

                    # Extract FSRS memory state
                    fsrs_state = fsrs_state_of(card)

                    # Get front content
                    if card.nid not in fronts:
                        fronts[card.nid] = None
                        try:
                            note = repo.col.get_note(card.nid)
                            fronts[card.nid] = note.fields[0] if note.fields else None
                        except Exception as e:
                            logger.debug(f"[stats] no note for cid={card.id}: {e}")
                    front = fronts[card.nid]

                    answer_dist, last_review, avg_time = revlog.get(cid, ({}, None, 0))
                    if last_review is None:
                        last_review = _optional_positive_int(
                            getattr(card, "last_review_time", None)
                        )

                    stats_list.append(
                        CardStatsAggregate(
                            card_id=card.id,
                            note_id=card.nid,
                            deck_name=deck_name,
                            lapses=card.lapses,
                            ease=card.factor,
                            interval=card.ivl,
                            due=card.due,
                            reps=card.reps,
                            fsrs=fsrs_state,
                            last_review=last_review,
                            average_time_ms=avg_time,
                            reviews=[],  # Populated on demand via get_review_history
                            front=front,
                            answer_distribution=dict(answer_dist),
                        )
                    )

        return stats_list

    async def get_review_history(self, cids: list[int]) -> list[ReviewEntry]:
        """Fetch review history from the revlog table.

        A failed read raises: no history would read as cards never reviewed.
        """
        if not cids:
            return []

        entries: list[ReviewEntry] = []

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col or repo.col.db is None:
                raise OSError("Anki collection did not open")

            # Query revlog for all cids
            # revlog columns: id, cid, usn, ease, ivl, lastIvl, factor, time, type...
            columns = []
            if repo.col.db:
                columns = [c[1] for c in repo.col.db.execute("PRAGMA table_info(revlog)")]
            has_data = "data" in columns

            col_subset = "id, cid, ease, ivl, lastIvl"
            if has_data:
                col_subset += ", data"

            import json

            unique_cids = list(dict.fromkeys(cids))
            ordered_entries: list[tuple[int, ReviewEntry]] = []

            for i in range(0, len(unique_cids), _SQL_CHUNK):
                chunk = unique_cids[i : i + _SQL_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                query = (
                    f"SELECT {col_subset} FROM revlog WHERE cid IN ({placeholders}) ORDER BY id ASC"
                )

                for row in repo.col.db.execute(query, *chunk):
                    # Indices:
                    # 0: id, 1: cid, 2: ease, 3: ivl, 4: lastIvl, 5: data (if the column exists)
                    data_idx = 5
                    s_at_review = None
                    d_at_review = None
                    r_at_review = None

                    if has_data and len(row) > data_idx and row[data_idx]:
                        try:
                            data = json.loads(row[data_idx])
                            s_at_review = data.get("s")
                            d_at_review = data.get("d")
                            r_at_review = data.get("r")
                        except Exception as e:
                            logger.debug(f"[stats] unreadable revlog data: {e}")

                    ordered_entries.append(
                        (
                            row[0],
                            ReviewEntry(
                                card_id=row[1],
                                review_time=row[0] // 1000,
                                rating=row[2],
                                interval=revlog_interval_days(row[3]),
                                last_interval=revlog_interval_days(row[4]),
                                stability=s_at_review,
                                difficulty=d_at_review,
                                retrievability=r_at_review,
                            ),
                        )
                    )

            if len(unique_cids) > _SQL_CHUNK:
                ordered_entries.sort(key=lambda item: item[0])
            entries.extend(entry for _, entry in ordered_entries)

        return entries

    async def close(self) -> None:
        """Close repository resources (no persistent connection for direct SQLite access)."""
        return None

    async def get_deck_params(self, deck_names: list[str]) -> dict[str, dict]:
        """Fetch FSRS parameters (desired retention, weights) for the given decks.

        A deck Anki does not hold (or whose config cannot be read) gets the defaults,
        matching `ConnectStatsRepository`; a failed read raises.
        """
        params: dict[str, dict] = {}

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                raise OSError("Anki collection did not open")

            for deck_name in deck_names:
                deck = repo.col.decks.by_name(deck_name)
                if not deck:
                    params[deck_name] = default_params()
                    continue
                # Get deck config (config id is 'conf')
                config = repo.col.decks.get_config(deck["conf"])
                params[deck_name] = deck_params(config) if config else default_params()

        return params

    @staticmethod
    def _cids_by_note(repo: AnkiRepository, nids: list) -> dict[int, list[int]]:
        """Card ids per note id, ascending, as find_cards("nid:N") returns them.

        One query per chunk of notes; a failed query raises.
        """
        cids_of: dict[int, list[int]] = {}
        wanted = list(dict.fromkeys(n for n in map(_as_int, nids) if n is not None))
        if repo.col is None or repo.col.db is None:
            return cids_of
        for i in range(0, len(wanted), _SQL_CHUNK):
            chunk = wanted[i : i + _SQL_CHUNK]
            query = (
                f"SELECT nid, id FROM cards WHERE nid IN ({','.join('?' * len(chunk))}) "
                "ORDER BY nid, id"
            )
            rows = repo.col.db.all(query, *chunk)
            for nid in chunk:
                cids_of[nid] = []
            for nid, cid in rows:
                cids_of[nid].append(cid)
        return cids_of

    @staticmethod
    def _revlog_aggregates(
        repo: AnkiRepository, cids: list[int]
    ) -> dict[int, tuple[dict[int, int], int | None, int]]:
        """Per card: answer counts by rating, last review (s), average time (ms).

        One grouped revlog query per chunk of cards. A card with no review is absent; a
        failed query raises, since its cards would read as never reviewed.
        """
        out: dict[int, tuple[dict[int, int], int | None, int]] = {}
        if repo.col is None or repo.col.db is None:
            return out
        unique = list(dict.fromkeys(cids))
        for i in range(0, len(unique), _SQL_CHUNK):
            chunk = unique[i : i + _SQL_CHUNK]
            query = (
                "SELECT cid, ease, COUNT(*), MAX(id), SUM(time) FROM revlog "
                f"WHERE cid IN ({','.join('?' * len(chunk))}) GROUP BY cid, ease"
            )
            rows = repo.col.db.all(query, *chunk)
            totals: dict[int, list[int]] = {}  # cid -> [reviews, time, newest id]
            dists: dict[int, dict[int, int]] = {}
            for cid, ease, count, newest, time_sum in rows:
                dists.setdefault(cid, {})[ease] = count
                total = totals.setdefault(cid, [0, 0, 0])
                total[0] += count
                total[1] += time_sum or 0
                total[2] = max(total[2], newest or 0)
            for cid, (count, time_sum, newest) in totals.items():
                # The mean review time, truncated by int().
                mean = time_sum / count if count else 0
                out[cid] = (
                    dists[cid],
                    newest // 1000 if newest else None,
                    int(mean) if mean else 0,
                )
        return out
