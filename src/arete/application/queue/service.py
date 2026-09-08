"""Queue orchestration service.

Encapsulates the shared workflow:
    due cards -> map NIDs -> build queue -> topo sort -> create deck

All three interface layers (CLI, MCP, HTTP) delegate to this service
instead of duplicating the orchestration logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from arete.application.queue.builder import (
    QueueBuildResult,
    build_dynamic_queue,
    build_simple_queue,
)
from arete.application.queue.graph_resolver import build_graph, topological_sort
from arete.domain.interfaces import AnkiBridge

# The one queue-algorithm vocabulary. CLI, HTTP and MCP all expose exactly this.
QueueAlgo = Literal["static", "dynamic", "simple"]

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """A single card in the study queue with display metadata."""

    position: int
    id: str
    title: str
    file: str
    is_prereq: bool


@dataclass
class QueueOrchestratorResult:
    """Rich result from the queue orchestration workflow.

    Contains everything the interface layers need to display results.
    """

    # Counts
    due_count: int = 0
    unmapped_count: int = 0

    # Algorithm used
    algo: QueueAlgo = "static"

    # Queue build result (from builder)
    build_result: QueueBuildResult | None = None

    # Enriched queue items (with titles and file paths)
    queue_items: list[QueueItem] = field(default_factory=list)

    # Deck creation
    deck_created: bool = False
    deck_name: str = ""
    deck_card_count: int = 0

    # Dry run flag
    dry_run: bool = False

    # Convenience accessors
    @property
    def prereq_count(self) -> int:
        return len(self.build_result.prereq_queue) if self.build_result else 0

    @property
    def main_count(self) -> int:
        return len(self.build_result.main_queue) if self.build_result else 0

    @property
    def missing_prereqs(self) -> list[str]:
        return self.build_result.missing_prereqs if self.build_result else []

    @property
    def cycles(self) -> list[list[str]]:
        return self.build_result.cycles if self.build_result else []

    @property
    def total_queued(self) -> int:
        return len(self.queue_items)


async def build_study_queue(  # noqa: PLR0913
    anki: AnkiBridge,
    vault_root: Path,
    *,
    deck: str | None = None,
    depth: int = 2,
    max_cards: int = 50,
    include_new: bool = False,
    cross_deck: bool = False,
    algo: QueueAlgo = "static",
    dry_run: bool = False,
    deck_name: str = "Arete::Queue",
    reschedule: bool = True,
    enrich: bool = True,
) -> QueueOrchestratorResult:
    """Orchestrate the full queue-building workflow.

    Steps:
        1. Fetch due cards from Anki
        2. Map NIDs to Arete IDs
        3. Build the dependency queue (static, dynamic, or simple)
        4. Optionally enrich with graph metadata (titles, files)
        5. Optionally create the filtered deck in Anki

    Args:
        anki: AnkiBridge instance (caller provides; factory is not our concern)
        vault_root: Path to the Obsidian vault
        deck: Optional deck name filter for due cards
        depth: Prerequisite search depth
        max_cards: Maximum cards in queue
        include_new: Include new (unreviewed) cards
        cross_deck: Pull prerequisites from other decks
        algo: Queue algorithm ("static", "dynamic", or "simple")
        dry_run: If True, skip deck creation
        deck_name: Name for the filtered deck
        reschedule: Whether to reschedule cards in the filtered deck
        enrich: Whether to enrich queue items with graph metadata

    Returns:
        QueueOrchestratorResult with everything interfaces need to display

    """
    result = QueueOrchestratorResult(algo=algo, dry_run=dry_run)

    # -- Step 1: Fetch due cards --
    nids = await anki.get_due_cards(deck, include_new=include_new)
    result.due_count = len(nids)

    if not nids:
        return result

    # -- Step 2: Map NIDs to Arete IDs --
    arete_ids = await anki.map_nids_to_arete_ids(nids)
    result.unmapped_count = len(nids) - len(arete_ids)

    valid_ids = [aid for aid in arete_ids if aid]
    if not valid_ids:
        return result

    # -- Step 3: Business rule — effective depth --
    # When --deck is specified without --cross-deck, don't walk prerequisites
    # outside the deck. This keeps the queue as an isolated set.
    effective_depth = depth if (not deck or cross_deck) else 0

    # -- Step 4: Build the queue --
    if algo == "dynamic":
        build_result = build_dynamic_queue(
            vault_root=vault_root,
            due_card_ids=valid_ids,
            depth=effective_depth,
            max_cards=max_cards,
        )
    else:
        # "static" and "simple" were two names for the same traversal: the weak/strong
        # split that separated them was never configured, so it never split anything.
        build_result = build_simple_queue(
            vault_root=vault_root,
            due_card_ids=valid_ids,
            depth=effective_depth,
            max_cards=max_cards,
        )

    result.build_result = build_result

    # -- Step 5: Determine final ordering --
    if algo == "dynamic" and build_result.ordered_queue:
        combined = build_result.ordered_queue
    else:
        all_ids = list(dict.fromkeys(build_result.prereq_queue + build_result.main_queue))
        graph = build_graph(vault_root)
        combined = topological_sort(graph, all_ids)

    # -- Step 6: Enrich with graph metadata --
    if enrich and combined:
        graph = build_graph(vault_root)
        prereq_set = set(build_result.prereq_queue)
        for idx, card_id in enumerate(combined, 1):
            node = graph.nodes.get(card_id)
            result.queue_items.append(
                QueueItem(
                    position=idx,
                    id=card_id,
                    title=node.title if node else card_id,
                    file=node.file_path if node else "",
                    is_prereq=card_id in prereq_set,
                )
            )
    elif combined:
        # Minimal items without graph lookup
        prereq_set = set(build_result.prereq_queue)
        for idx, card_id in enumerate(combined, 1):
            result.queue_items.append(
                QueueItem(
                    position=idx,
                    id=card_id,
                    title=card_id,
                    file="",
                    is_prereq=card_id in prereq_set,
                )
            )

    # -- Step 7: Create filtered deck (unless dry run) --
    if not dry_run and combined:
        cids = await anki.get_card_ids_for_arete_ids(combined)
        valid_cids = [c for c in cids if c]
        if valid_cids:
            ok = await anki.create_topo_deck(deck_name, valid_cids, reschedule=reschedule)
            result.deck_created = ok
            result.deck_name = deck_name
            result.deck_card_count = len(valid_cids)

    return result
