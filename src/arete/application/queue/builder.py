"""Queue builder for dependency-aware study sessions.

Builds ordered study queues by:
1. Walking requires edges backward from due cards
2. Filtering for weak prerequisites
3. Topologically sorting for proper learning order
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from arete.application.queue.graph_resolver import build_graph, topological_sort
from arete.domain.constants import DEFAULT_MAX_QUEUE_SIZE, DEFAULT_PREREQ_DEPTH
from arete.domain.graph import DependencyGraph

logger = logging.getLogger(__name__)


def build_simple_queue(
    vault_root: Path,
    due_card_ids: list[str],
    depth: int = DEFAULT_PREREQ_DEPTH,
    max_cards: int = DEFAULT_MAX_QUEUE_SIZE,
) -> "QueueBuildResult":
    """Build a simple study queue from due cards.

    Collects prerequisites up to depth, then topological sort.

    Args:
        vault_root: Path to the Obsidian vault
        due_card_ids: List of Arete IDs that are due for review
        depth: How many prerequisite levels to include
        max_cards: Maximum cards in queue

    Returns:
        QueueBuildResult with ordered queues and diagnostics

    """
    from arete.application.queue.graph_resolver import detect_cycles

    graph = build_graph(vault_root)

    # Collect all prerequisites for due cards
    all_prereqs: set[str] = set()
    missing_prereqs: list[str] = []

    def _note_missing(card_id: str) -> None:
        for ref in graph.unresolved_refs.get(card_id, []):
            if ref not in missing_prereqs:
                missing_prereqs.append(ref)

    for card_id in due_card_ids:
        _note_missing(card_id)
        for prereq_id in _collect_prereqs(graph, card_id, depth, set()):
            if prereq_id in graph.nodes:
                all_prereqs.add(prereq_id)
                _note_missing(prereq_id)

    # Remove due cards from prereqs (they'll be in main queue)
    all_prereqs -= set(due_card_ids)

    # Limit size. max_cards bounds the whole queue; sorted() so that which prereqs
    # survive the cap does not depend on set iteration order.
    prereq_list = sorted(all_prereqs)
    if len(prereq_list) + len(due_card_ids) > max_cards:
        prereq_list = prereq_list[: max(0, max_cards - len(due_card_ids))]

    # Topological sort both queues
    prereq_queue = topological_sort(graph, prereq_list)
    main_queue = topological_sort(graph, due_card_ids)

    # Detect cycles
    cycles = detect_cycles(graph)

    return QueueBuildResult(
        prereq_queue=prereq_queue,
        main_queue=main_queue,
        missing_prereqs=missing_prereqs,
        cycles=cycles,
    )


@dataclass
class QueueBuildResult:
    """Result of queue building operation."""

    prereq_queue: list[str]  # Prerequisites to study first (topo sorted)
    main_queue: list[str]  # Original due cards (topo sorted)
    missing_prereqs: list[str]  # Referenced prereqs not found in graph
    cycles: list[list[str]]  # Co-requisite groups detected
    ordered_queue: list[str] | None = None  # Optional full ordering for advanced algorithms


def build_dynamic_queue(
    vault_root: Path,
    due_card_ids: list[str],
    depth: int = DEFAULT_PREREQ_DEPTH,
    max_cards: int = DEFAULT_MAX_QUEUE_SIZE,
    card_stats: dict[str, dict] | None = None,
) -> QueueBuildResult:
    """Build a queue using a dynamic ready-frontier ordering heuristic.

    Layered on top of build_simple_queue:
    - Reuse dependency discovery
    - Order candidates by a ready-frontier policy that prioritizes:
      1) Cards that unlock more due descendants
      2) Weaker cards -- only when the caller supplies card_stats
      3) Deterministic lexical tie-breaks
    """
    base = build_simple_queue(
        vault_root=vault_root,
        due_card_ids=due_card_ids,
        depth=depth,
        max_cards=max_cards,
    )

    graph = build_graph(vault_root)
    due_set = {cid for cid in due_card_ids if cid in graph.nodes}
    candidates = [
        cid for cid in dict.fromkeys(base.prereq_queue + base.main_queue) if cid in graph.nodes
    ]

    ordered = _dynamic_frontier_order(
        graph=graph,
        candidate_ids=candidates,
        due_set=due_set,
        card_stats=card_stats,
    )

    prereq_queue = [cid for cid in ordered if cid not in due_set]
    main_queue = [cid for cid in ordered if cid in due_set]

    return QueueBuildResult(
        prereq_queue=prereq_queue,
        main_queue=main_queue,
        missing_prereqs=base.missing_prereqs,
        cycles=base.cycles,
        ordered_queue=ordered,
    )


def _collect_prereqs(
    graph: DependencyGraph,
    card_id: str,
    depth: int,
    visited: set[str],
) -> set[str]:
    """Recursively collect prerequisites up to a given depth."""
    if depth <= 0 or card_id in visited:
        return set()

    visited.add(card_id)
    prereqs: set[str] = set()

    for prereq_id in graph.get_prerequisites(card_id):
        prereqs.add(prereq_id)
        prereqs.update(_collect_prereqs(graph, prereq_id, depth - 1, visited))

    return prereqs


def _dynamic_frontier_order(
    graph: DependencyGraph,
    candidate_ids: list[str],
    due_set: set[str],
    card_stats: dict[str, dict] | None,
) -> list[str]:
    """Produce a deterministic frontier-based order for candidate cards."""
    if not candidate_ids:
        return []

    valid = [cid for cid in candidate_ids if cid in graph.nodes]
    valid_set = set(valid)

    in_degree: dict[str, int] = dict.fromkeys(valid, 0)
    dependents: dict[str, list[str]] = {cid: [] for cid in valid}

    for cid in valid:
        for prereq_id in graph.get_prerequisites(cid):
            if prereq_id in valid_set:
                in_degree[cid] += 1
                dependents[prereq_id].append(cid)

    # Probe for cycles in the candidate subgraph; fallback keeps behavior safe.
    probe_degree = dict(in_degree)
    probe_ready = [cid for cid in valid if probe_degree[cid] == 0]
    seen = 0
    while probe_ready:
        node = probe_ready.pop()
        seen += 1
        for dep in dependents[node]:
            probe_degree[dep] -= 1
            if probe_degree[dep] == 0:
                probe_ready.append(dep)
    if seen != len(valid):
        logger.warning(
            "Dynamic queue candidate subgraph has cycles; falling back to topological_sort."
        )
        return topological_sort(graph, valid)

    # Build a deterministic topological order (lexical ties) for reachability DP.
    topo_order: list[str] = []
    topo_degree = dict(in_degree)
    topo_ready = sorted([cid for cid in valid if topo_degree[cid] == 0])
    while topo_ready:
        node = topo_ready.pop(0)
        topo_order.append(node)
        for dep in sorted(dependents[node]):
            topo_degree[dep] -= 1
            if topo_degree[dep] == 0:
                topo_ready.append(dep)
        topo_ready.sort()

    # due_reach[node] = due cards that this node can unlock downstream (including itself if due)
    due_reach: dict[str, set[str]] = {cid: set() for cid in valid}
    for node in reversed(topo_order):
        reach = {node} if node in due_set else set()
        for dep in dependents[node]:
            reach.update(due_reach[dep])
        due_reach[node] = reach

    remaining = dict(in_degree)
    ready = [cid for cid in valid if remaining[cid] == 0]
    ordered: list[str] = []

    def score(card_id: str) -> float:
        unlock_score = float(len(due_reach[card_id]))
        weak_score = _weakness_score(card_id, card_stats)
        prereq_bonus = 0.5 if (card_id not in due_set and unlock_score > 0) else 0.0
        due_bonus = 0.25 if card_id in due_set else 0.0
        return (2.0 * unlock_score) + weak_score + prereq_bonus + due_bonus

    while ready:
        ready.sort(key=lambda cid: (-score(cid), cid))
        node = ready.pop(0)
        ordered.append(node)

        for dep in dependents[node]:
            remaining[dep] -= 1
            if remaining[dep] == 0:
                ready.append(dep)

    return ordered


def _weakness_score(card_id: str, card_stats: dict[str, dict] | None) -> float:
    """Score card weakness for dynamic frontier prioritization.

    Returns 0.0 when the caller supplies no stats, which is what every interface
    currently does -- the dynamic order is then purely structural.
    """
    if not card_stats:
        return 0.0

    stats = card_stats.get(card_id)
    if not isinstance(stats, dict):
        return 0.0

    score = 0.0

    stability = stats.get("stability")
    if isinstance(stability, (int, float)):
        score += 1.0 / (1.0 + max(float(stability), 0.0))

    lapses = stats.get("lapses")
    if isinstance(lapses, (int, float)):
        score += max(float(lapses), 0.0) * 0.15

    reps = stats.get("reps")
    if isinstance(reps, (int, float)):
        r = max(float(reps), 0.0)
        if r < 10.0:
            score += (10.0 - r) * 0.05

    interval = stats.get("interval")
    if isinstance(interval, (int, float)):
        score += 1.0 / (1.0 + max(float(interval), 0.0))

    return score
