"""Learning Insights Service.

Wraps AnkiBridge to provide learning statistics and note name cleaning.
"""

import logging
from typing import Any

from arete.domain.stats.models import LearningStats

logger = logging.getLogger(__name__)


class LearningInsightsService:
    """Service to interact with Anki and generate learning insights."""

    def __init__(self, anki_bridge: Any):
        """Initialize LearningInsightsService."""
        self.anki = anki_bridge

    @staticmethod
    def clean_note_name(raw_name: str) -> str:
        """Clean raw _obsidian_source strings.

        Example: 'Obsidian Vault|My Note.md|89' -> 'My Note'.
        """
        import re

        # 1. Strip HTML if any
        name = re.sub("<[^<]+?>", "", raw_name)

        # 2. Handle the "Vault|Path|ID" or "Vault|Path" format
        if "|" in name:
            parts = name.split("|")
            # If multiple pipes, usually Vault|Path|ID
            if len(parts) >= 2:
                # Part 1 is usually the path/name
                name = parts[1]
            else:
                name = parts[0]

        # 3. Strip .md extension
        if name.lower().endswith(".md"):
            name = name[:-3]

        # 4. Take only the basename if it's a path
        if "/" in name:
            name = name.split("/")[-1]
        elif "\\" in name:
            name = name.split("\\")[-1]

        return name.strip()

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
