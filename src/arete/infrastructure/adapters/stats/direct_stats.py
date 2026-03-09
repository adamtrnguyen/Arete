"""Direct Stats Repository — Infrastructure adapter for Anki's SQLite database.

Implements StatsRepository by querying the Anki collection directly.
"""

import logging
from pathlib import Path

from arete.domain.stats.models import CardStatsAggregate, FsrsMemoryState, ReviewEntry
from arete.domain.stats.ports import StatsRepository
from arete.infrastructure.anki.repository import AnkiRepository

logger = logging.getLogger(__name__)


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

            for nid in nids:
                try:
                    cids = repo.col.find_cards(f"nid:{nid}")

                    for cid in cids:
                        card = repo.col.get_card(cid)
                        deck = repo.col.decks.get(card.did)
                        deck_name = deck["name"] if deck else "Unknown"

                        # Extract FSRS memory state
                        fsrs_state: FsrsMemoryState | None = None
                        if hasattr(card, "memory_state") and card.memory_state:
                            ms = card.memory_state
                            difficulty = (
                                ms.difficulty  # FSRS uses 1-10 scale natively
                                if hasattr(ms, "difficulty")
                                else None
                            )
                            stability = ms.stability if hasattr(ms, "stability") else None

                            if stability is not None and difficulty is not None:
                                fsrs_state = FsrsMemoryState(
                                    stability=stability,
                                    difficulty=difficulty,
                                    retrievability=None,  # Computed by application layer
                                )

                        # Get last review time from revlog
                        last_review = self._get_last_review_time(repo, cid)

                        # Get front content
                        front = None
                        try:
                            note = repo.col.get_note(card.nid)
                            front = note.fields[0] if note.fields else None
                        except Exception:
                            pass

                        # Get answer distribution from revlog
                        answer_dist = self._get_answer_distribution(repo, cid)

                        # Get average time
                        avg_time = self._get_average_time(repo, cid)

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
                                answer_distribution=answer_dist,
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
                        except Exception:
                            pass

                    entries.append(
                        ReviewEntry(
                            card_id=row[1],
                            review_time=row[0] // 1000,
                            rating=row[2],
                            interval=row[3],
                            last_interval=row[4],
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

    def _get_answer_distribution(self, repo: AnkiRepository, cid: int) -> dict[int, int]:
        """Get counts of each answer button rating for a card."""
        try:
            if repo.col is None or repo.col.db is None:
                return {}

            dist: dict[int, int] = {}
            query = "SELECT ease, COUNT(*) FROM revlog WHERE cid = ? GROUP BY ease"
            for rating, count in repo.col.db.execute(query, cid):
                dist[rating] = count
            return dist
        except Exception:
            return {}

    def _get_last_review_time(self, repo: AnkiRepository, cid: int) -> int | None:
        """Get the most recent review time for a card."""
        try:
            if repo.col is None or repo.col.db is None:
                return None
            result = repo.col.db.scalar("SELECT MAX(id) FROM revlog WHERE cid = ?", cid)
            if result:
                return result // 1000  # Convert ms to seconds
        except Exception:
            pass
        return None

    def _get_average_time(self, repo: AnkiRepository, cid: int) -> int:
        """Get average time taken (ms) for a card."""
        try:
            if repo.col is None or repo.col.db is None:
                return 0
            # time limit is usually capped at 60s (60000ms) in revlog logic but here we just avg
            result = repo.col.db.scalar("SELECT AVG(time) FROM revlog WHERE cid = ?", cid)
            return int(result) if result else 0
        except Exception:
            pass
        return 0
