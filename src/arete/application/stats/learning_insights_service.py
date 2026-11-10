"""Learning Insights Service.

Wraps AnkiBridge to provide learning statistics and note name cleaning.
"""

import logging

from arete.domain.interfaces import CardStatsPort
from arete.domain.stats.models import LearningStats

logger = logging.getLogger(__name__)


class LearningInsightsService:
    """Service to interact with Anki and generate learning insights."""

    def __init__(self, anki_bridge: CardStatsPort):
        """Initialize LearningInsightsService."""
        self.anki = anki_bridge

    @staticmethod
    def clean_note_name(raw_name: str) -> str:
        """The note's name from its `_obsidian_source`, or the text itself.

        A source is `vault|path|line|arete_id`, as the sync writes it: its name is the
        file's basename without `.md` ('Vault|Folder/My Note.md|89|arete_…' -> 'My Note').
        Text in any other shape (a first field, 'Unknown') has no source and is returned
        with its HTML stripped.
        """
        import re

        name = re.sub("<[^<]+?>", "", raw_name).strip()
        parts = name.split("|")
        if len(parts) != 4 or not parts[2].isdigit():
            return name
        path = parts[1]
        if path.lower().endswith(".md"):
            path = path[:-3]
        return path.rsplit("/", 1)[-1].strip()

    async def get_learning_insights(self, lapse_threshold: int = 3) -> LearningStats:
        """Fetch stats via the bridge (Connect or Direct)."""
        try:
            insights = await self.anki.get_learning_insights(lapse_threshold=lapse_threshold)

            # Clean names for the agent's consumption
            for note in insights.problematic_notes:
                note.note_name = self.clean_note_name(note.note_name)

            return insights
        except Exception as e:
            logger.error(f"Failed to fetch real stats via bridge: {e}", exc_info=True)
            raise
