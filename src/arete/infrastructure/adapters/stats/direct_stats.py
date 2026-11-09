"""Direct Stats Repository — Infrastructure adapter for Anki's SQLite database.

Implements StatsRepository by querying the Anki collection directly.
"""

import logging
from pathlib import Path
from typing import Any, cast

from arete.domain.stats.models import CardStatsAggregate, ReviewEntry, revlog_interval_days
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.anki.fsrs import fsrs_state_of
from arete.infrastructure.anki.repository import AnkiRepository

logger = logging.getLogger(__name__)

_SQL_CHUNK = 500  # ids per IN (...) query, well under SQLite's variable limit


class DirectStatsRepository(StatsRepository):
    """Fetches card statistics directly from Anki's SQLite database.

    Accesses FSRS memory state via card.memory_state when available.
    """

    def __init__(self, anki_base: Path | None = None):
        """Initialize DirectStatsRepository."""
        self.anki_base = anki_base

    async def get_card_stats(self, nids: list[int]) -> list[CardStatsAggregate]:
        """Fetch comprehensive stats for cards belonging to the given note IDs."""
        if not nids:
            return []

        stats_list: list[CardStatsAggregate] = []

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                logger.warning("Could not open Anki collection")
                return []

            # Grouped reads: the cards of every note and the revlog aggregates of every
            # card, one query per chunk; deck and note lookups once per deck and note.
            cids_of = self._cids_by_note(repo, nids)
            revlog = self._revlog_aggregates(repo, [c for cids in cids_of.values() for c in cids])
            deck_names: dict[int, str] = {}
            fronts: dict[int, str | None] = {}

            for nid in nids:
                try:
                    cids = cids_of[int(nid)]

                    for cid in cids:
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

                except Exception as e:
                    logger.warning(f"Failed to fetch stats for nid={nid}: {e}")

        return stats_list

    async def get_review_history(self, cids: list[int]) -> list[ReviewEntry]:
        """Fetch review history from the revlog table."""
        if not cids:
            return []

        entries: list[ReviewEntry] = []

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return []

            # Query revlog for all cids
            # revlog columns: id, cid, usn, ease, ivl, lastIvl, factor, time, type...
            columns = []
            if repo.col.db:
                columns = [c[1] for c in repo.col.db.execute("PRAGMA table_info(revlog)")]
            has_data = "data" in columns

            # Select essential columns + lastIvl + time
            col_subset = "id, cid, ease, ivl, lastIvl, time, type"
            if has_data:
                col_subset += ", data"

            placeholders = ",".join("?" * len(cids))
            query = f"SELECT {col_subset} FROM revlog WHERE cid IN ({placeholders}) ORDER BY id ASC"

            try:
                import json

                if repo.col.db is None:
                    return []
                for row in repo.col.db.execute(query, *cids):
                    # Indices:
                    # 0: id, 1: cid, 2: ease, 3: ivl, 4: lastIvl, 5: time, 6: type
                    # 7: data (if exists) via 'col_subset' length logic?

                    data_idx = 7
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

                    entries.append(
                        ReviewEntry(
                            card_id=row[1],
                            review_time=row[0] // 1000,
                            rating=row[2],
                            interval=revlog_interval_days(row[3]),
                            last_interval=revlog_interval_days(row[4]),
                            time_taken=row[5],
                            review_type=row[6],
                            stability=s_at_review,
                            difficulty=d_at_review,
                            retrievability=r_at_review,
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch review history: {e}")

        return entries

    async def get_deck_params(self, deck_names: list[str]) -> dict[str, dict]:
        """Fetch FSRS parameters (desired retention, weights) for the given decks."""
        params: dict[str, dict] = {}

        with AnkiRepository(self.anki_base) as repo:
            if not repo.col:
                return {}

            for deck_name in deck_names:
                try:
                    deck = repo.col.decks.by_name(deck_name)
                    if not deck:
                        continue

                    # Get deck config (config id is 'conf')
                    config = repo.col.decks.get_config(deck["conf"])
                    if not config:
                        continue

                    # Look for FSRS settings in the config
                    # Modern Anki: config['fsrs']
                    fsrs = config.get("fsrs", {})

                    default_retention = fsrs.get("desiredRetention", 0.9)
                    params[deck_name] = {
                        "desired_retention": config.get("desiredRetention", default_retention),
                        "weights": fsrs.get("w", []),
                        "sm2_retention": config.get("sm2Retention", 0.9),  # Fallback for non-FSRS
                    }
                except Exception as e:
                    logger.warning(f"Failed to fetch params for deck {deck_name}: {e}")

        return params

    @staticmethod
    def _cids_by_note(repo: AnkiRepository, nids: list) -> dict[int, list[int]]:
        """Card ids per note id, ascending, as find_cards("nid:N") returns them.

        One query per chunk of notes. A chunk whose query fails is left out, so its notes
        are logged and skipped by the caller as a failed find_cards was.
        """
        cids_of: dict[int, list[int]] = {}
        wanted: list[int] = []
        for nid in nids:
            try:
                wanted.append(int(nid))
            except (TypeError, ValueError):
                continue  # the caller reports it
        wanted = list(dict.fromkeys(wanted))
        if repo.col is None or repo.col.db is None:
            return cids_of
        for i in range(0, len(wanted), _SQL_CHUNK):
            chunk = wanted[i : i + _SQL_CHUNK]
            query = (
                f"SELECT nid, id FROM cards WHERE nid IN ({','.join('?' * len(chunk))}) "
                "ORDER BY nid, id"
            )
            try:
                rows = repo.col.db.all(query, *chunk)
            except Exception as e:
                logger.warning(f"[stats] card query failed for {len(chunk)} notes: {e}")
                continue
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
        chunk whose query fails is logged and left out, so its cards get the defaults.
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
            try:
                rows = repo.col.db.all(query, *chunk)
            except Exception as e:
                logger.warning(f"[stats] revlog query failed for {len(chunk)} cards: {e}")
                continue
            totals: dict[int, list[int]] = {}  # cid -> [reviews, time, newest id]
            dists: dict[int, dict[int, int]] = {}
            for cid, ease, count, newest, time_sum in rows:
                dists.setdefault(cid, {})[ease] = count
                total = totals.setdefault(cid, [0, 0, 0])
                total[0] += count
                total[1] += time_sum or 0
                total[2] = max(total[2], newest or 0)
            for cid, (count, time_sum, newest) in totals.items():
                # AVG(time) is the float mean, truncated by int() as the per-card query was
                mean = time_sum / count if count else 0
                out[cid] = (
                    dists[cid],
                    newest // 1000 if newest else None,
                    int(mean) if mean else 0,
                )
        return out
