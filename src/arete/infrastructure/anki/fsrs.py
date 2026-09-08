"""FSRS memory state of an Anki card, in the domain's units.

The domain contract (FsrsMemoryState) is difficulty normalised to 0-1; Anki stores
1-10. Both direct-DB readers (AnkiDirectAdapter, DirectStatsRepository) go through
here so they cannot drift apart again.
"""

from __future__ import annotations

from typing import Any

from arete.domain.constants import FSRS_DIFFICULTY_SCALE
from arete.domain.stats.models import FsrsMemoryState


def fsrs_state_of(card: Any) -> FsrsMemoryState | None:
    """Return the card's FSRS state, or None if it has never been reviewed under FSRS."""
    ms = card.memory_state  # Optional[FsrsMemoryState protobuf] on anki 25.9.2
    if not ms:
        return None
    return FsrsMemoryState(
        stability=ms.stability,
        difficulty=ms.difficulty / FSRS_DIFFICULTY_SCALE,
        retrievability=None,  # computed by the application layer
    )
