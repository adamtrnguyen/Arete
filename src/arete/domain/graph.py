"""Domain types for dependency graph.

These types represent the structure of card dependencies
without any persistence or resolution logic.
"""

from dataclasses import dataclass, field

import networkx as nx


@dataclass(frozen=True)
class CardNode:
    """A node in the dependency graph representing a single card.

    Attributes:
        id: Stable Arete ID (e.g., arete_01JH8Y3ZK4QJ9W6E2N8F6M0P5R)
        title: Display name (typically the Front field or filename)
        file_path: Path to source markdown file
        line_number: Line number in source file for navigation
    """

    id: str
    title: str
    file_path: str
    line_number: int


@dataclass
class DependencyGraph:
    """Complete dependency graph for a vault.

    Nodes are cards, edges are requires/related relationships.

    Edge convention: add_requires("A", "B") means "A requires B".
    In the internal nx.DiGraph, the edge is B → A (prerequisite points to dependent).
    This means:
      - predecessors(A) = prerequisites of A
      - successors(B) = dependents of B
      - topological_sort naturally gives prerequisites before dependents
    """

    nodes: dict[str, CardNode] = field(default_factory=dict)
    related: dict[str, list[str]] = field(default_factory=dict)  # id → [related ids]
    unresolved_refs: dict[str, list[str]] = field(default_factory=dict)  # id → [unresolved refs]
    _graph: nx.DiGraph = field(default_factory=nx.DiGraph, repr=False)

    @property
    def edge_count(self) -> int:
        """Total number of requires edges."""
        return self._graph.number_of_edges()

    def add_node(self, node: CardNode) -> None:
        """Add a card node to the graph."""
        self.nodes[node.id] = node
        self._graph.add_node(node.id)
        if node.id not in self.related:
            self.related[node.id] = []
        if node.id not in self.unresolved_refs:
            self.unresolved_refs[node.id] = []

    def add_unresolved(self, from_id: str, ref: str) -> None:
        """Track an unresolved reference."""
        if from_id not in self.unresolved_refs:
            self.unresolved_refs[from_id] = []
        if ref not in self.unresolved_refs[from_id]:
            self.unresolved_refs[from_id].append(ref)

    def add_requires(self, from_id: str, to_id: str) -> None:
        """Add a 'requires' edge: from_id requires to_id."""
        if not self._graph.has_edge(to_id, from_id):
            self._graph.add_edge(to_id, from_id)

    def add_related(self, from_id: str, to_id: str) -> None:
        """Add a 'related' edge: from_id is related to to_id."""
        if from_id not in self.related:
            self.related[from_id] = []
        if to_id not in self.related[from_id]:
            self.related[from_id].append(to_id)

    def get_prerequisites(self, card_id: str) -> list[str]:
        """Get all direct prerequisites for a card."""
        if card_id not in self._graph:
            return []
        return list(self._graph.predecessors(card_id))

    def get_dependents(self, card_id: str) -> list[str]:
        """Get all cards that require this card (reverse lookup)."""
        if card_id not in self._graph:
            return []
        return list(self._graph.successors(card_id))

    def get_related(self, card_id: str) -> list[str]:
        """Get all related cards."""
        return self.related.get(card_id, [])


@dataclass
class LocalGraphResult:
    """Result of a local graph query centered on a specific card.

    Used by the UI to render the dependency visualization.
    """

    center: CardNode
    prerequisites: list[CardNode]  # Upstream requires (depth-limited)
    dependents: list[CardNode]  # Downstream requires (depth-limited)
    related: list[CardNode]  # Related cards
    cycles: list[list[str]]  # Groups of co-requisite card IDs
