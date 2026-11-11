"""Graph resolver for building dependency graphs from vault files.

Parses YAML frontmatter to extract deps.requires and deps.related,
builds the full dependency graph, and provides traversal utilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any

from arete.application.utils.common import sanitize
from arete.application.utils.fs import iter_markdown_files
from arete.application.utils.text import normalize_filename, parse_frontmatter
from arete.domain.graph import CardNode, DependencyGraph
from arete.domain.ownership import id_key

if TYPE_CHECKING:
    import networkx as nx

# networkx is imported inside the functions that use it: every CLI command imports this
# module through the queue service, and most of them never touch a graph.

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
class DuplicateIdEntry:
    """An Arete id used by more than one card: only the first one is in the graph."""

    card_id: str
    files: list[str]


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
    duplicate_ids: list[DuplicateIdEntry] = field(default_factory=list)


logger = logging.getLogger(__name__)


def build_graph(vault_root: Path) -> DependencyGraph:
    """Build a complete dependency graph from all markdown files in the vault.

    Scans for files with cards that have `id` and `deps` fields.

    Dependency references support two formats:
    - arete_XXX: Direct card ID lookup
    - basename: All cards in the file with that basename (e.g., "algebra" -> algebra.md)
    """
    graph = DependencyGraph()

    # First pass: collect all cards and index them BY FILE. A basename can name more
    # than one file (math/Intro.md, bio/Intro.md), so it maps to files, not cards.
    file_cards: dict[str, list[str]] = {}  # file path -> card ids
    by_basename: dict[str, list[str]] = {}  # NFC basename -> file paths
    by_relpath: dict[str, str] = {}  # NFC vault-relative path without .md -> file path
    card_file: dict[str, str] = {}  # card id -> file path
    pending_deps: list[tuple[str, list[str], list[str]]] = []  # (card_id, requires, related)
    # `id_key` -> the id as first written: ids differing only in case are one id, as in
    # the sync, so the second is a duplicate.
    first_by_key: dict[str, str] = {}

    for md_path in iter_markdown_files(vault_root):
        try:
            text = md_path.read_text(encoding="utf-8")
            if not _may_hold_cards(text):
                continue
            meta, _ = parse_frontmatter(text)

            if not meta or "__yaml_error__" in meta:
                continue

            if meta.get("arete") is not True:
                continue

            cards = meta.get("cards", [])
            if not isinstance(cards, list):
                continue

            # Normalize to NFC so user-typed YAML refs (NFC) match macOS filesystem
            # basenames (NFD).
            fkey = str(md_path)
            file_cards.setdefault(fkey, [])
            by_basename.setdefault(normalize_filename(md_path.stem), []).append(fkey)
            rel = md_path.relative_to(vault_root).with_suffix("").as_posix()
            by_relpath[normalize_filename(rel)] = fkey
            graph.card_decks[fkey] = _card_decks(meta, cards)

            for card in cards:
                if not isinstance(card, dict):
                    continue

                card_id = sanitize(card.get("id", "")).strip()  # as the sync reads it
                if not card_id:
                    continue  # Skip cards without Arete ID

                title = card.get("Front") or card.get("Text") or card_id

                # Get line number if available
                line_number = card.get("__line__", 1)

                node = CardNode(
                    id=card_id,
                    title=str(title)[:100],  # Truncate long titles
                    file_path=str(md_path),
                    line_number=line_number,
                )
                first_id = first_by_key.setdefault(id_key(card_id), card_id)
                if first_id != card_id or card_id in graph.nodes:
                    # Keep the first card and record the duplicate; never overwrite.
                    first = graph.nodes[first_id].file_path
                    files = graph.duplicate_ids.setdefault(first_id, [first])
                    files.append(str(md_path))
                    logger.warning(f"Duplicate Arete id {card_id} in {files}")
                    continue
                graph.add_node(node)
                file_cards[fkey].append(card_id)
                card_file[card_id] = fkey

                # Collect deps for second pass
                deps = card.get("deps", {})
                if isinstance(deps, dict):
                    requires = _as_refs(deps.get("requires"), card_id, graph)
                    related = _as_refs(deps.get("related"), card_id, graph)
                    if requires or related:
                        pending_deps.append((card_id, requires, related))

        except Exception as e:
            # The file's cards are now absent from the graph: record it so `graph check`
            # and queue builds can report it instead of silently working on a partial graph.
            logger.warning(f"Failed to parse {md_path}: {e}")
            graph.skipped_files.append((str(md_path), str(e)))
            continue

    index = _FileIndex(file_cards, by_basename, by_relpath, vault_root)

    # Second pass: resolve references and add edges
    for card_id, requires, related in pending_deps:
        own_file = card_file.get(card_id)
        for refs, add_edge in ((requires, graph.add_requires), (related, graph.add_related)):
            for ref in refs:
                for target_id in _resolve_reference(ref, card_id, index, graph):
                    # A note-level ref never points at the card's own file. Compared by
                    # FILE, not basename: bio/Intro requiring math/Intro is a real edge.
                    if target_id == card_id or (
                        not ref.startswith("arete_") and card_file.get(target_id) == own_file
                    ):
                        continue
                    add_edge(card_id, target_id)

    return graph


def _frontmatter_slice(text: str) -> str | None:
    """The text `parse_frontmatter` hands to YAML, found by string search alone.

    Bounds follow `parse_frontmatter`: after a leading BOM, the first line stripped is
    `---`, and the block ends at the first later line that is `---` once right-stripped.
    Returns "" when the text has no opening line (the parser reads nothing), and None when
    the closing line is missing, so the caller leaves that case to the parser.
    """
    text = text.lstrip("﻿")
    first_end = text.find("\n")
    first_line = text if first_end == -1 else text[:first_end]
    if first_line.strip() != "---":
        return ""
    if first_end == -1:
        return None
    # A closing line starts with `---` in column 0, so only those lines are candidates.
    pos = first_end
    while (pos := text.find("\n---", pos)) != -1:
        line_end = text.find("\n", pos + 1)
        line = text[pos + 1 :] if line_end == -1 else text[pos + 1 : line_end]
        if line.rstrip() == "---":
            return text[first_end + 1 : pos]
        pos += 1
    return None


def _may_hold_cards(text: str) -> bool:
    """False only for a note whose frontmatter cannot set the `arete` key.

    In every YAML spelling of the key (plain, quoted, `arete :`) the word is in the
    frontmatter, except a double-quoted key with escapes, which needs a backslash there.
    Sync's test, `"arete:" in text`, is narrower: it misses `"arete": true`. Only the
    frontmatter is searched, so math in a note's body does not force a parse.
    """
    fm = _frontmatter_slice(text)
    return fm is None or "arete" in fm or "\\" in fm


@dataclass
class _FileIndex:
    file_cards: dict[str, list[str]]
    by_basename: dict[str, list[str]]
    by_relpath: dict[str, str]
    vault_root: Path


def _as_refs(value: Any, card_id: str, graph: DependencyGraph) -> list[str]:
    """Normalize a `requires`/`related` value to a list of string refs.

    A scalar (`requires: Algebra`) counts as a one-item list and an int (`[2024]`, as YAML
    reads it) as its string. Anything that cannot be a reference is reported.
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    refs: list[str] = []
    for item in items:
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            refs.append(str(item))
        elif item is not None:
            graph.add_unresolved(card_id, f"malformed ref: {item!r}")
    return refs


def _resolve_reference(
    ref: str,
    card_id: str,
    index: _FileIndex,
    graph: DependencyGraph,
) -> list[str]:
    """Resolve a dependency reference to card ID(s).

    - arete_XXX: that card.
    - basename: all cards in the ONE file with that name. When several files share it,
      nothing is guessed: the ref is reported as ambiguous.
    - folder/basename: all cards in the file at that vault-relative path, which is how to
      pick one of several namesakes.

    Tracks unresolved references in the graph.
    """
    if ref.startswith("arete_"):
        if ref in graph.nodes:
            return [ref]
        logger.warning(f"Dependency reference '{ref}' not found in graph")
        graph.add_unresolved(card_id, ref)
        return []

    ref_normalized = normalize_filename(ref)
    if "/" in ref_normalized:
        path = index.by_relpath.get(ref_normalized.strip("/"))
        files = [path] if path else []
    else:
        files = index.by_basename.get(ref_normalized, [])

    if len(files) == 1:
        return index.file_cards[files[0]]
    if len(files) > 1:
        rels = sorted(Path(f).relative_to(index.vault_root).as_posix() for f in files)
        logger.warning(f"Dependency reference '{ref}' is ambiguous: {rels}")
        graph.add_unresolved(
            card_id, f"{ref} (ambiguous: {', '.join(rels)}; use a folder/name ref)"
        )
        return []
    logger.warning(f"Dependency reference '{ref}' - no file with basename '{ref}' found")
    graph.add_unresolved(card_id, ref)
    return []


def detect_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Detect all cycles in the requires graph.

    Returns a list of strongly connected components (cycles).
    Each cycle is a list of card IDs that form a co-requisite group.
    """
    import networkx as nx

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
    import networkx as nx

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
    """`card_ids` that are in `graph`, prerequisites before dependents.

    A cycle is kept together as one group; inside it, cards are ordered by depth in the
    vault graph, then by input order. Ids not in the graph are dropped.
    """
    import networkx as nx

    # Filter to only requested cards that exist, keeping input order: iterating a set
    # would make the result depend on the hash seed.
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
    # cycle elsewhere in the vault does not switch the tiebreaker off.
    depth = _component_depths(graph._graph)
    # Secondary tiebreaker: input order (preserves Anki scheduling among equals).
    id_order = {cid: i for i, cid in enumerate(valid_ids)}

    def key(n: str) -> tuple[int, int]:
        return depth.get(n, 0), id_order[n]

    # Collapse each cycle into one group: groups are sorted by dependency, so a cycle
    # cannot reorder prerequisites outside it, and only the cards inside a cycle are
    # ordered by the tiebreaker alone.
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
    import networkx as nx

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
    """A new graph holding only the cards in `deck` or one of its subdecks.

    A card's deck is its own ``deck:`` when set, else the file's -- the rule sync uses.
    It matches when it is ``deck`` or ``deck::...``, as Anki's ``deck:`` search does.
    Edges and unresolved refs are carried over for matching nodes.
    """
    keep_ids: set[str] = set()

    # Decks come from the build. A file the graph holds no record for (a graph built by
    # hand) is read and parsed here, once.
    for fpath in sorted({node.file_path for node in graph.nodes.values()}):
        try:
            entries = graph.card_decks.get(fpath)
            if entries is None:
                text = Path(fpath).read_text(encoding="utf-8")
                meta, _ = parse_frontmatter(text)
                entries = _card_decks(meta, (meta or {}).get("cards", []))
            # Exact deck or subdeck, never a substring ("Math" must not keep "Applied
            # Mathematics"), and the card's own deck wins over the file's, as in sync.
            for cid, effective in entries:
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


def _card_decks(meta: Any, cards: Any) -> list[tuple[Any, str]]:
    """(card id, effective deck) for each card mapping: the card's deck, else the file's."""
    file_deck = str((meta or {}).get("deck") or "")
    return [
        (card.get("id"), str(card.get("deck") or file_deck))
        for card in cards
        if isinstance(card, dict)
    ]


def _in_deck(card_deck: str, deck: str) -> bool:
    return card_deck == deck or card_deck.startswith(deck + "::")


def vault_relative(path: str, vault_root: PurePath) -> str:
    """`path` as the vault-relative string, with `/`, that every output names a note by.

    The same note gets the same string on every OS, `folder/Note.md`, which is the path
    Obsidian's API takes. `path` is read with the vault root's flavour (Windows or POSIX).
    A path outside the vault is returned as it is.
    """
    try:
        return type(vault_root)(path).relative_to(vault_root).as_posix()
    except ValueError:
        return path


def check_graph_health(
    vault_root: Path,
    deck_filter: str | None = None,
) -> GraphHealthResult:
    """Build the vault graph and report cycles, isolated cards, unresolved refs and counts.

    `deck_filter` restricts the report to that deck and its subdecks.
    """
    full = build_graph(vault_root)

    def rel(path: str) -> str:
        return vault_relative(path, vault_root)

    skipped = [f"{rel(path)}: {err}" for path, err in full.skipped_files]

    graph = filter_graph_by_deck(full, deck_filter) if deck_filter else full
    scope = set(graph.nodes)

    # Analyse the full graph and report only what touches the chosen deck: on the
    # filtered graph a card whose prerequisite sits in another deck looks isolated.
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
                    file=rel(full.nodes[cid].file_path) if cid in full.nodes else "unknown",
                )
                for cid in cycle
            ]
        )

    # Isolated nodes
    isolated_entries = [
        IsolatedEntry(
            card_id=cid,
            title=graph.nodes[cid].title,
            file=rel(graph.nodes[cid].file_path),
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
                    file=rel(node.file_path),
                    missing_refs=refs,
                )
            )

    # Root nodes: have dependents but no prerequisites
    roots = [
        cid for cid in graph.nodes if not graph.get_prerequisites(cid) and graph.get_dependents(cid)
    ]

    duplicates = [
        DuplicateIdEntry(card_id=cid, files=[rel(f) for f in files])
        for cid, files in sorted(full.duplicate_ids.items())
        if cid in scope or not deck_filter
    ]
    ok = len(cycles_raw) == 0 and len(unresolved_entries) == 0 and not skipped and not duplicates

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
        duplicate_ids=duplicates,
    )


@dataclass
class GraphExportNode:
    """A card as the Obsidian plugin draws it; `file` is vault-relative, posix."""

    id: str
    title: str
    file: str
    line: int


@dataclass
class GraphExport:
    """The whole resolved graph, for clients that draw it instead of resolving it.

    Edges are `[card, target]`: `requires` points at the prerequisite. The Obsidian plugin
    reads this rather than resolving references itself, so the two cannot drift.
    """

    nodes: list[GraphExportNode]
    requires: list[list[str]]
    related: list[list[str]]
    cycles: list[list[str]]
    unresolved_refs: dict[str, list[str]]
    duplicate_ids: dict[str, list[str]]
    skipped_files: list[str]


def export_graph(vault_root: Path) -> GraphExport:
    """Build the vault graph and flatten it for a client."""
    graph = build_graph(vault_root)

    def rel(path: str) -> str:
        return vault_relative(path, vault_root)

    nodes = [
        GraphExportNode(id=n.id, title=n.title, file=rel(n.file_path), line=n.line_number)
        for n in graph.nodes.values()
    ]
    requires = [[cid, pre] for cid in graph.nodes for pre in graph.get_prerequisites(cid)]
    related = [[cid, rel_id] for cid in graph.nodes for rel_id in graph.get_related(cid)]
    return GraphExport(
        nodes=nodes,
        requires=requires,
        related=related,
        cycles=detect_cycles(graph),
        unresolved_refs={cid: refs for cid, refs in graph.unresolved_refs.items() if refs},
        duplicate_ids={cid: [rel(f) for f in files] for cid, files in graph.duplicate_ids.items()},
        skipped_files=[f"{rel(p)}: {err}" for p, err in graph.skipped_files],
    )
