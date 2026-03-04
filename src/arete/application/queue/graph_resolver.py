"""Graph resolver for building dependency graphs from vault files.

Parses YAML frontmatter to extract deps.requires and deps.related,
builds the full dependency graph, and provides traversal utilities.
"""

import logging
from pathlib import Path

import networkx as nx

from arete.application.utils.fs import iter_markdown_files
from arete.application.utils.text import parse_frontmatter
from arete.domain.graph import CardNode, DependencyGraph, LocalGraphResult

logger = logging.getLogger(__name__)


def build_graph(vault_root: Path) -> DependencyGraph:
    """Build a complete dependency graph from all markdown files in the vault.

    Scans for files with cards that have `id` and `deps` fields.

    Dependency references support two formats:
    - arete_XXX: Direct card ID lookup
    - basename: All cards in the file with that basename (e.g., "algebra" -> algebra.md)
    """
    graph = DependencyGraph()

    # First pass: collect all cards and build file index
    file_index: dict[str, list[str]] = {}  # basename -> list of card IDs
    pending_deps: list[tuple[str, list[str], list[str]]] = []  # (card_id, requires, related)

    for md_path in iter_markdown_files(vault_root):
        try:
            text = md_path.read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(text)

            if not meta or "__yaml_error__" in meta:
                continue

            if meta.get("arete") is not True:
                continue

            cards = meta.get("cards", [])
            if not isinstance(cards, list):
                continue

            # Get file basename for index
            basename = md_path.stem  # "algebra.md" -> "algebra"
            if basename not in file_index:
                file_index[basename] = []

            for card in cards:
                if not isinstance(card, dict):
                    continue

                card_id = card.get("id")
                if not card_id:
                    continue  # Skip cards without Arete ID

                # Extract title from Front field or use ID
                # V2: fields are at root. V1 (internal model): fields might be nested.
                fields = card.get("fields", {})
                if isinstance(fields, dict) and "Front" in fields:
                    title = fields.get("Front")
                else:
                    # Fallback to checking root level (V2)
                    title = card.get("Front") or card.get("title") or card_id

                # Get line number if available
                line_number = card.get("__line__", 1)

                node = CardNode(
                    id=card_id,
                    title=str(title)[:100],  # Truncate long titles
                    file_path=str(md_path),
                    line_number=line_number,
                )
                graph.add_node(node)

                # Add to file index
                file_index[basename].append(card_id)

                # Collect deps for second pass
                deps = card.get("deps", {})
                if isinstance(deps, dict):
                    requires = deps.get("requires", [])
                    related = deps.get("related", [])
                    if requires or related:
                        pending_deps.append(
                            (
                                card_id,
                                requires if isinstance(requires, list) else [],
                                related if isinstance(related, list) else [],
                            )
                        )

        except Exception as e:
            logger.warning(f"Failed to parse {md_path}: {e}")
            continue

    # Build reverse index: card_id -> basename of its file
    card_to_basename: dict[str, str] = {}
    for basename, card_ids_in_file in file_index.items():
        for cid in card_ids_in_file:
            card_to_basename[cid] = basename

    # Second pass: resolve references and add edges
    for card_id, requires, related in pending_deps:
        own_basename = card_to_basename.get(card_id)
        for ref in requires:
            if isinstance(ref, str):
                resolved = _resolve_reference(ref, card_id, file_index, graph)
                for target_id in resolved:
                    # Skip same-file cards when resolving basename deps
                    if target_id == card_id:
                        continue
                    if not ref.startswith("arete_") and own_basename and ref == own_basename:
                        continue
                    graph.add_requires(card_id, target_id)

        for ref in related:
            if isinstance(ref, str):
                resolved = _resolve_reference(ref, card_id, file_index, graph)
                for target_id in resolved:
                    if target_id == card_id:
                        continue
                    if not ref.startswith("arete_") and own_basename and ref == own_basename:
                        continue
                    graph.add_related(card_id, target_id)

    return graph


def _resolve_reference(
    ref: str,
    card_id: str,
    file_index: dict[str, list[str]],
    graph: DependencyGraph,
) -> list[str]:
    """Resolve a dependency reference to card ID(s).

    - arete_XXX: Direct card ID (returns single-element list if exists)
    - basename: All cards in that file (returns list of all card IDs)

    Tracks unresolved references in the graph.
    """
    if ref.startswith("arete_"):
        # Direct card ID lookup
        if ref in graph.nodes:
            return [ref]
        else:
            logger.warning(f"Dependency reference '{ref}' not found in graph")
            graph.add_unresolved(card_id, ref)
            return []
    else:
        # Note basename -> all cards in that file
        if ref in file_index:
            return file_index[ref]
        else:
            logger.warning(f"Dependency reference '{ref}' - no file with basename '{ref}' found")
            graph.add_unresolved(card_id, ref)
            return []


def get_local_graph(
    graph: DependencyGraph,
    card_id: str,
    depth: int = 2,
    max_nodes: int = 150,
) -> LocalGraphResult | None:
    """Get a local subgraph centered on a specific card.

    Args:
        graph: The full dependency graph
        card_id: Center card's Arete ID
        depth: Maximum depth to traverse (default: 2)
        max_nodes: Maximum nodes to include (default: 150)

    Returns:
        LocalGraphResult with prerequisites, dependents, and related cards,
        or None if card_id not found.

    """
    if card_id not in graph.nodes:
        return None

    center = graph.nodes[card_id]
    prereqs: set[str] = set()
    dependents: set[str] = set()
    related_ids: set[str] = set()

    # Walk prerequisites (backward along requires edges)
    def walk_prereqs(cid: str, current_depth: int) -> None:
        if current_depth > depth or len(prereqs) >= max_nodes:
            return
        for prereq_id in graph.get_prerequisites(cid):
            if len(prereqs) >= max_nodes:
                break
            if prereq_id not in prereqs and prereq_id in graph.nodes:
                prereqs.add(prereq_id)
                walk_prereqs(prereq_id, current_depth + 1)

    # Walk dependents (forward along requires edges)
    def walk_dependents(cid: str, current_depth: int) -> None:
        if current_depth > depth or len(dependents) >= max_nodes:
            return
        for dep_id in graph.get_dependents(cid):
            if len(dependents) >= max_nodes:
                break
            if dep_id not in dependents and dep_id in graph.nodes:
                dependents.add(dep_id)
                walk_dependents(dep_id, current_depth + 1)

    walk_prereqs(card_id, 1)
    walk_dependents(card_id, 1)

    # Get related (only direct, no traversal)
    for rel_id in graph.get_related(card_id):
        if rel_id in graph.nodes:
            related_ids.add(rel_id)

    # Detect cycles involving the center card
    cycles = detect_cycles_for_card(graph, card_id)

    return LocalGraphResult(
        center=center,
        prerequisites=[graph.nodes[pid] for pid in prereqs],
        dependents=[graph.nodes[did] for did in dependents],
        related=[graph.nodes[rid] for rid in related_ids],
        cycles=cycles,
    )


def detect_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Detect all cycles in the requires graph.

    Returns a list of strongly connected components (cycles).
    Each cycle is a list of card IDs that form a co-requisite group.
    """
    if nx.is_directed_acyclic_graph(graph._graph):
        return []

    return [
        list(scc)
        for scc in nx.strongly_connected_components(graph._graph)
        if len(scc) > 1
    ]


def detect_cycles_for_card(graph: DependencyGraph, card_id: str) -> list[list[str]]:
    """Detect cycles that include a specific card.

    Uses strongly connected components to find cycles containing card_id.
    """
    if card_id not in graph.nodes:
        return []

    if card_id not in graph._graph:
        return []

    return [
        list(scc)
        for scc in nx.strongly_connected_components(graph._graph)
        if len(scc) > 1 and card_id in scc
    ]


def find_isolated_nodes(graph: DependencyGraph) -> list[str]:
    """Find cards with no requires AND no dependents (completely disconnected)."""
    return [
        cid
        for cid in graph.nodes
        if not graph.get_prerequisites(cid) and not graph.get_dependents(cid)
    ]


def find_connected_components(graph: DependencyGraph) -> list[set[str]]:
    """Find connected components treating requires edges as undirected."""
    # Build a graph that includes all nodes from graph.nodes
    # (some may only be in graph.nodes dict, not in _graph)
    g = graph._graph.copy()
    for nid in graph.nodes:
        if nid not in g:
            g.add_node(nid)

    components = [
        {n for n in comp if n in graph.nodes}
        for comp in nx.weakly_connected_components(g)
    ]
    return sorted([c for c in components if c], key=len, reverse=True)


def topological_sort(
    graph: DependencyGraph,
    card_ids: list[str],
) -> list[str]:
    """Topologically sort a subset of cards based on requires edges.

    Cards without prerequisites come first.
    If cycles exist, they are treated as a group (arbitrary order within).

    Args:
        graph: The dependency graph
        card_ids: Cards to sort

    Returns:
        Sorted list of card IDs (prerequisites before dependents)

    """
    # Filter to only requested cards that exist
    valid_ids = {cid for cid in card_ids if cid in graph.nodes}

    # Build a fresh subgraph with only the requested nodes and their edges
    sub = nx.DiGraph()
    sub.add_nodes_from(valid_ids)
    for card_id in valid_ids:
        for prereq in graph.get_prerequisites(card_id):
            if prereq in valid_ids:
                sub.add_edge(prereq, card_id)

    try:
        # Compute depth in the FULL graph as a tiebreaker.
        # Depth = longest path from any root to this node, which is a proxy
        # for "how advanced is this concept" (correlates with chapter order).
        # Among cards at the same topological level in the subgraph, lower
        # depth (more fundamental) cards come first.
        depth: dict[str, int] = {}
        try:
            for node in nx.topological_sort(graph._graph):
                preds = list(graph._graph.predecessors(node))
                depth[node] = (max(depth.get(p, 0) for p in preds) + 1) if preds else 0
        except nx.NetworkXUnfeasible:
            pass  # Full graph has cycles; skip depth, fall through to input order

        # Secondary tiebreaker: input order (preserves Anki scheduling among
        # cards at same depth).
        id_order = {cid: i for i, cid in enumerate(card_ids) if cid in valid_ids}

        return list(
            nx.lexicographical_topological_sort(
                sub,
                key=lambda n: (depth.get(n, 0), id_order.get(n, float("inf"))),
            )
        )
    except nx.NetworkXUnfeasible:
        # If there are cycles, fall back to partial ordering
        logger.warning("Cycle detected in card dependencies, using original order")
        return list(valid_ids)
