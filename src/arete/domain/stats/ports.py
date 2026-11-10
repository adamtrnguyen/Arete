"""Ports (interfaces) for stats retrieval.

These define the contract that infrastructure adapters must implement.
Application services depend on these abstractions, not concrete implementations.
"""

from abc import ABC, abstractmethod

from .models import CardStatsAggregate, ReviewEntry


class StatsRepository(ABC):
    """Port for fetching card statistics from Anki.

    Implementations:
        - DirectStatsRepository: Queries Anki's SQLite database directly.
        - ConnectStatsRepository: Uses AnkiConnect HTTP API.
    """

    @abstractmethod
    async def get_card_stats(self, nids: list[int]) -> list[CardStatsAggregate]:
        """Stats for every card of the notes `nids`, with FSRS data when Anki has it."""
        pass

    @abstractmethod
    async def get_review_history(self, cids: list[int]) -> list[ReviewEntry]:
        """Review log entries for the cards `cids`, oldest first."""
        pass

    @abstractmethod
    async def get_deck_params(self, deck_names: list[str]) -> dict[str, dict]:
        """FSRS parameters (desired retention, weights) per deck name."""
        pass
