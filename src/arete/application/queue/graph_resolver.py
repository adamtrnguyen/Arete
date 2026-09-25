"""Graph resolver for building dependency graphs from vault files.

Parses YAML frontmatter to extract deps.requires and deps.related,
builds the full dependency graph, and provides traversal utilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from arete.application.utils.fs import iter_markdown_files
from arete.application.utils.text import normalize_filename, parse_frontmatter
from arete.domain.graph import CardNode, DependencyGraph

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CycleEntry:
    """A single card participating in a cycle."""

    card_id: str
    title: str
    file: str


@dataclass
class IsolatedEntry:
    """An isolated card with no deps in or out."""

    card_id: str
    title: str
    file: str


@dataclass
class UnresolvedEntry:
    """A card with unresolved dependency references."""

    card_id: str
    title: str
    file: str
    missing_refs: list[str]


@dataclass
class GraphHealthResult:
    """Structured result of a graph health check."""

    ok: bool
    total_nodes: int
    total_edges: int
    roots: int
    components: int
    cycles: list[list[CycleEntry]]
    isolated_nodes: list[IsolatedEntry]
    unresolved_refs: list[UnresolvedEntry]
    deck_filter: str | None = None
    skipped_files: list[str] = field(default_factory=list)  # "path: error"


@dataclass
class SubgraphNodeEntry:
    """A card node in the subgraph with its relationships."""

    id: str
    title: str
    file: str
    basename: str
    line: int
    requires: list[str]
    required_by: list[str]
    related: list[str]
    unresolved: list[str]


@dataclass
class ExternalNodeEntry:
    """An external card referenced by the batch but not part of it."""

    id: str
    title: str
    file: str
    basename: str


@dataclass
class SubgraphResult:
    """Structured result of a subgraph query for a batch of files."""

    batch_cards: int
    external_deps: int
    cycles_involving_batch: list[list[str]]
    nodes: list[SubgraphNodeEntry]
    external_nodes: list[ExternalNodeEntry]


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

            # Get file basename for index. Normalize to NFC so user-typed
            # YAML refs (NFC) match macOS filesystem basenames (NFD).
            basename = normalize_filename(md_path.stem)  # "algebra.md" -> "algebra"
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
            # The file's cards are now absent from the graph: record it so `graph check`
            # and queue builds can report it instead of silently working on a partial graph.
            logger.warning(f"Failed to parse {md_path}: {e}")
            graph.skipped_files.append((str(md_path), str(e)))
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
                    if (
                        not ref.startswith("arete_")
                        and own_basename
                        and normalize_filename(ref) == own_basename
                    ):
                        continue
                    graph.add_requires(card_id, target_id)

        for ref in related:
            if isinstance(ref, str):
                resolved = _resolve_reference(ref, card_id, file_index, graph)
                for target_id in resolved:
                    if target_id == card_id:
                        continue
                    if (
                        not ref.startswith("arete_")
                        and own_basename
                        and normalize_filename(ref) == own_basename
                    ):
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
        # Note basename -> all cards in that file. Normalize to match
        # the NFC-normalized keys in file_index.
        ref_normalized = normalize_filename(ref)
        if ref_normalized in file_index:
            return file_index[ref_normalized]
        else:
            logger.warning(f"Dependency reference '{ref}' - no file with basename '{ref}' found")
            graph.add_unresolved(card_id, ref)
            return []


def detect_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Detect all cycles in the requires graph.

    Returns a list of strongly connected components (cycles).
    Each cycle is a list of card IDs that form a co-requisite group.
    """
    if nx.is_directed_acyclic_graph(graph._graph):
        return []

    return [list(scc) for scc in nx.strongly_connected_components(graph._graph) if len(scc) > 1]


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
        {n for n in comp if n in graph.nodes} for comp in nx.weakly_connected_components(g)
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
    # Filter to only requested cards that exist, keeping input order (a set here made
    # the result depend on the hash seed).
    valid_ids = list(dict.fromkeys(cid for cid in card_ids if cid in graph.nodes))
    valid_set = set(valid_ids)

    # Build a fresh subgraph with only the requested nodes and their edges
    sub = nx.DiGraph()
    sub.add_nodes_from(valid_ids)
    for card_id in valid_ids:
        for prereq in graph.get_prerequisites(card_id):
            if prereq in valid_set:
                sub.add_edge(prereq, card_id)

    # Depth = longest path from any root, a proxy for "how advanced is this concept"
    # (correlates with chapter order). Computed over strongly connected components, so a
    # cycle elsewhere in the vault no longer switches the tiebreaker off.
    depth = _component_depths(graph._graph)
    # Secondary tiebreaker: input order (preserves Anki scheduling among equals).
    id_order = {cid: i for i, cid in enumerate(valid_ids)}

    def key(n: str) -> tuple[int, int]:
        return depth.get(n, 0), id_order[n]

    # G1 (2026-09-25): a cycle used to make the whole queue fall back to set order, so
    # prerequisites unrelated to the cycle could come after their dependents. Collapse
    # each cycle into one group instead: groups are sorted by dependency, and only the
    # cards inside a cycle are ordered by the tiebreaker alone.
    cond = nx.condensation(sub)
    members = nx.get_node_attributes(cond, "members")
    if any(len(m) > 1 for m in members.values()):
        logger.warning("Cycle detected in card dependencies; ordering each cycle as a group")
    ordered: list[str] = []
    for group in nx.lexicographical_topological_sort(
        cond, key=lambda c: min(key(n) for n in members[c])
    ):
        ordered.extend(sorted(members[group], key=key))
    return ordered


def _component_depths(g: nx.DiGraph) -> dict[str, int]:
    """Longest-path depth of every node, with each cycle treated as a single node."""
    cond = nx.condensation(g)
    members = nx.get_node_attributes(cond, "members")
    comp_depth: dict[int, int] = {}
    for c in nx.topological_sort(cond):
        preds = list(cond.predecessors(c))
        comp_depth[c] = (max(comp_depth[p] for p in preds) + 1) if preds else 0
    return {n: comp_depth[c] for c, ms in members.items() for n in ms}


# ---------------------------------------------------------------------------
# High-level analysis functions
# ---------------------------------------------------------------------------


def filter_graph_by_deck(graph: DependencyGraph, deck: str) -> DependencyGraph:
    """Filter a dependency graph to only include cards matching a deck.

    A card's deck is its own ``deck:`` when set, else the file's -- the rule sync uses.
    It matches when it is ``deck`` or a subdeck of it (``deck::...``), as Anki's
    ``deck:`` search does. Edges and unresolved refs are carried over for matching nodes.

    Args:
        graph: The full dependency graph.
        deck: Deck name; its subdecks match too.

    Returns:
        A new DependencyGraph containing only the matching nodes.

    """
    keep_ids: set[str] = set()

    # Each file is parsed once; a card's deck comes from its own frontmatter entry.
    for fpath in sorted({node.file_path for node in graph.nodes.values()}):
        try:
            text = Path(fpath).read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(text)
            file_deck = str((meta or {}).get("deck") or "")
            # G4 (2026-09-25): this was a substring test ("Math" kept "Applied
            # Mathematics"), and a matching FILE deck overrode the card's own deck --
            # the reverse of sync (parser: card deck wins).
            for card in (meta or {}).get("cards", []):
                if not isinstance(card, dict):
                    continue
                cid = card.get("id")
                effective = str(card.get("deck") or file_deck)
                if cid in graph.nodes and _in_deck(effective, deck):
                    keep_ids.add(cid)
        except Exception as e:
            logger.warning(f"[deck-filter] skipping {fpath}: {e}")
            continue

    # Rebuild graph with only matching nodes
    filtered = DependencyGraph()
    for cid in keep_ids:
        if cid in graph.nodes:
            filtered.add_node(graph.nodes[cid])
    for cid in keep_ids:
        for prereq in graph.get_prerequisites(cid):
            if prereq in keep_ids:
                filtered.add_requires(cid, prereq)
        for rel in graph.get_related(cid):
            if rel in keep_ids:
                filtered.add_related(cid, rel)
    # Carry over unresolved refs
    for cid in keep_ids:
        for ref in graph.unresolved_refs.get(cid, []):
            filtered.add_unresolved(cid, ref)

    return filtered


def _in_deck(card_deck: str, deck: str) -> bool:
    return card_deck == deck or card_deck.startswith(deck + "::")


def check_graph_health(
    vault_root: Path,
    deck_filter: str | None = None,
) -> GraphHealthResult:
    """Build the dependency graph and run all health checks.

    Args:
        vault_root: Path to the vault root directory.
        deck_filter: Optional deck (and its subdecks) to restrict the report to.

    Returns:
        A :class:`GraphHealthResult` with cycles, isolated nodes,
        unresolved refs, and summary counts.

    """
    full = build_graph(vault_root)
    skipped = [f"{path}: {err}" for path, err in full.skipped_files]

    graph = filter_graph_by_deck(full, deck_filter) if deck_filter else full
    scope = set(graph.nodes)

    # G8 (2026-09-25): analysed on the FILTERED graph, a card whose prerequisite sits in
    # another deck lost that edge and was reported "isolated". Analyse the full graph
    # and report only what touches the chosen deck.
    cycles_raw = [c for c in detect_cycles(full) if scope & set(c)]
    isolated_ids = [cid for cid in find_isolated_nodes(full) if cid in scope]
    components = find_connected_components(graph)

    # Enrich cycles with titles/files
    enriched_cycles: list[list[CycleEntry]] = []
    for cycle in cycles_raw:
        enriched_cycles.append(
            [
                CycleEntry(
                    card_id=cid,
                    title=full.nodes[cid].title if cid in full.nodes else cid,
                    file=full.nodes[cid].file_path if cid in full.nodes else "unknown",
                )
                for cid in cycle
            ]
        )

    # Isolated nodes
    isolated_entries = [
        IsolatedEntry(
            card_id=cid,
            title=graph.nodes[cid].title,
            file=graph.nodes[cid].file_path,
        )
        for cid in isolated_ids
    ]

    # Unresolved refs
    unresolved_entries: list[UnresolvedEntry] = []
    for cid, refs in graph.unresolved_refs.items():
        if refs and cid in graph.nodes:
            node = graph.nodes[cid]
            unresolved_entries.append(
                UnresolvedEntry(
                    card_id=cid,
                    title=node.title,
                    file=node.file_path,
                    missing_refs=refs,
                )
            )

    # Root nodes: have dependents but no prerequisites
    roots = [
        cid for cid in graph.nodes if not graph.get_prerequisites(cid) and graph.get_dependents(cid)
    ]

    ok = len(cycles_raw) == 0 and len(unresolved_entries) == 0 and not skipped

    return GraphHealthResult(
        ok=ok,
        total_nodes=len(graph.nodes),
        total_edges=graph.edge_count,
        roots=len(roots),
        components=len(components),
        cycles=enriched_cycles,
        isolated_nodes=isolated_entries,
        unresolved_refs=unresolved_entries,
        deck_filter=deck_filter,
        skipped_files=skipped,
    )


def get_subgraph_for_files(
    vault_root: Path,
    file_paths: list[str],
) -> SubgraphResult:
    """Build a dependency subgraph for a batch of files.

    Returns all cards in the given files with their deps, plus edges
    to/from cards outside the batch (external deps).

    Args:
        vault_root: Path to the vault root directory.
        file_paths: Absolute paths to markdown files.

    Returns:
        A :class:`SubgraphResult` with batch nodes, external deps,
        and batch-relevant cycles.

    """
    graph = build_graph(vault_root)
    paths_set = set(file_paths)

    # Identify cards in the batch
    batch_ids: set[str] = set()
    for cid, node in graph.nodes.items():
        if node.file_path in paths_set:
            batch_ids.add(cid)

    if not batch_ids:
        return SubgraphResult(
            batch_cards=0,
            external_deps=0,
            cycles_involving_batch=[],
            nodes=[],
            external_nodes=[],
        )

    # Build node list with full info
    nodes: list[SubgraphNodeEntry] = []
    for cid in sorted(batch_ids):
        node = graph.nodes[cid]
        nodes.append(
            SubgraphNodeEntry(
                id=cid,
                title=node.title,
                file=node.file_path,
                basename=Path(node.file_path).stem,
                line=node.line_number,
                requires=graph.get_prerequisites(cid),
                required_by=graph.get_dependents(cid),
                related=graph.get_related(cid),
                unresolved=graph.unresolved_refs.get(cid, []),
            )
        )

    # External deps: cards outside the batch that batch cards reference
    external_ids: set[str] = set()
    for cid in batch_ids:
        for prereq in graph.get_prerequisites(cid):
            if prereq not in batch_ids:
                external_ids.add(prereq)
        for dep in graph.get_dependents(cid):
            if dep not in batch_ids:
                external_ids.add(dep)

    external_nodes: list[ExternalNodeEntry] = []
    for cid in sorted(external_ids):
        if cid in graph.nodes:
            node = graph.nodes[cid]
            external_nodes.append(
                ExternalNodeEntry(
                    id=cid,
                    title=node.title,
                    file=node.file_path,
                    basename=Path(node.file_path).stem,
                )
            )

    # Check for cycles within the batch
    cycles_raw = detect_cycles(graph)
    batch_cycles = [c for c in cycles_raw if any(cid in batch_ids for cid in c)]

    return SubgraphResult(
        batch_cards=len(nodes),
        external_deps=len(external_nodes),
        cycles_involving_batch=batch_cycles,
        nodes=nodes,
        external_nodes=external_nodes,
    )
