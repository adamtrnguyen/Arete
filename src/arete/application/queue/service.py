"""Queue orchestration service.

Encapsulates the shared workflow:
    due cards -> map NIDs -> build queue -> topo sort -> create deck

The CLI delegates to this service
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
from arete.application.queue.graph_resolver import build_graph, topological_sort, vault_relative
from arete.domain.interfaces import QueuePort

# The one queue-algorithm vocabulary. The CLI exposes exactly this.
QueueAlgo = Literal["static", "dynamic"]

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """A single card in the study queue with display metadata."""

    position: int
    id: str
    title: str
    file: str  # the note, vault-relative with "/" (what Obsidian's getAbstractFileByPath takes)
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
    def dropped_prereqs(self) -> list[str]:
        return self.build_result.dropped_prereqs if self.build_result else []

    @property
    def cycles(self) -> list[list[str]]:
        return self.build_result.cycles if self.build_result else []

    @property
    def total_queued(self) -> int:
        return len(self.queue_items)


async def build_study_queue(  # noqa: PLR0913
    anki: QueuePort,
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
    """Fetch due cards, map them to Arete ids, build the queue, and create the filtered deck.

    `algo` is "static" (prerequisite order) or "dynamic" (ready frontier).
    `cross_deck` pulls prerequisites from other decks; `enrich` adds titles and files from
    the graph; `dry_run` creates no deck. The result holds everything an interface shows.
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
    # One graph per request: the builder, the final sort and the enrich step share it.
    graph = build_graph(vault_root)
    if algo == "dynamic":
        build_result = build_dynamic_queue(
            vault_root=vault_root,
            due_card_ids=valid_ids,
            depth=effective_depth,
            max_cards=max_cards,
            graph=graph,
        )
    else:
        build_result = build_simple_queue(
            vault_root=vault_root,
            due_card_ids=valid_ids,
            depth=effective_depth,
            max_cards=max_cards,
            graph=graph,
        )

    result.build_result = build_result

    # -- Step 5: Determine final ordering --
    if algo == "dynamic" and build_result.ordered_queue:
        combined = build_result.ordered_queue
    else:
        all_ids = list(dict.fromkeys(build_result.prereq_queue + build_result.main_queue))
        combined = topological_sort(graph, all_ids)

    # -- Step 6: Enrich with graph metadata --
    if enrich and combined:
        prereq_set = set(build_result.prereq_queue)
        for idx, card_id in enumerate(combined, 1):
            node = graph.nodes.get(card_id)
            result.queue_items.append(
                QueueItem(
                    position=idx,
                    id=card_id,
                    title=node.title if node else card_id,
                    file=vault_relative(node.file_path, vault_root) if node else "",
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
        made = await create_queue_deck(anki, combined, deck_name=deck_name, reschedule=reschedule)
        if made.card_count:
            result.deck_created = made.ok
            result.deck_name = deck_name
            result.deck_card_count = made.card_count

    return result


@dataclass
class QueueDeckResult:
    """What `create_queue_deck` did: `ok`, a sentence for the user, and the cards it sent."""

    ok: bool
    message: str
    card_count: int = 0


async def create_queue_deck(
    anki: QueuePort,
    arete_ids: list[str],
    *,
    deck_name: str,
    reschedule: bool = True,
) -> QueueDeckResult:
    """Fill the filtered deck `deck_name` with the cards of `arete_ids`, in that order.

    An id with no Anki card is skipped; with none left, no deck is made.
    """
    cids = [c for c in await anki.get_card_ids_for_arete_ids(arete_ids) if c]
    if not cids:
        return QueueDeckResult(False, "No matching Anki cards found. Have you synced?")
    if not await anki.create_topo_deck(deck_name, cids, reschedule=reschedule):
        return QueueDeckResult(False, "Failed to create deck (check logs).", len(cids))
    return QueueDeckResult(True, f"Created deck '{deck_name}' with {len(cids)} cards.", len(cids))
