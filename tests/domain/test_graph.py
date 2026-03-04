"""Tests for arete.domain.graph — CardNode and DependencyGraph."""

import pytest

from arete.domain.graph import CardNode, DependencyGraph, LocalGraphResult


# ---------------------------------------------------------------------------
# CardNode
# ---------------------------------------------------------------------------


class TestCardNode:
    """CardNode is a frozen dataclass representing a graph vertex."""

    def test_creation_all_fields(self):
        node = CardNode(
            id="arete_01ABC",
            title="What is X?",
            file_path="/vault/concept.md",
            line_number=42,
        )
        assert node.id == "arete_01ABC"
        assert node.title == "What is X?"
        assert node.file_path == "/vault/concept.md"
        assert node.line_number == 42

    def test_frozen_raises_on_mutation(self):
        node = CardNode(id="a", title="t", file_path="f", line_number=1)
        with pytest.raises(AttributeError):
            node.id = "b"  # type: ignore[misc]

    def test_equality_same_values(self):
        a = CardNode(id="x", title="t", file_path="f", line_number=1)
        b = CardNode(id="x", title="t", file_path="f", line_number=1)
        assert a == b

    def test_inequality_different_id(self):
        a = CardNode(id="x", title="t", file_path="f", line_number=1)
        b = CardNode(id="y", title="t", file_path="f", line_number=1)
        assert a != b

    def test_hashable(self):
        """Frozen dataclasses are hashable — useful for sets/dicts."""
        node = CardNode(id="x", title="t", file_path="f", line_number=1)
        s = {node}
        assert node in s


# ---------------------------------------------------------------------------
# DependencyGraph — node operations
# ---------------------------------------------------------------------------


def _make_node(id_: str, title: str = "card") -> CardNode:
    """Helper to create a CardNode with minimal boilerplate."""
    return CardNode(id=id_, title=title, file_path="test.md", line_number=1)


class TestDependencyGraphNodes:
    """Adding nodes and inspecting basic node state."""

    def test_add_single_node(self):
        g = DependencyGraph()
        node = _make_node("A")
        g.add_node(node)

        assert "A" in g.nodes
        assert g.nodes["A"] is node
        assert g.edge_count == 0

    def test_add_multiple_nodes(self):
        g = DependencyGraph()
        for label in ("A", "B", "C"):
            g.add_node(_make_node(label))

        assert len(g.nodes) == 3
        assert g.edge_count == 0

    def test_add_duplicate_node_overwrites(self):
        """Adding a node with the same id replaces the entry in nodes dict."""
        g = DependencyGraph()
        g.add_node(_make_node("A", title="first"))
        g.add_node(_make_node("A", title="second"))

        assert g.nodes["A"].title == "second"
        # The nx graph should still have exactly one node "A"
        assert len(list(g._graph.nodes)) == 1

    def test_add_node_initialises_related_and_unresolved(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))

        assert g.related["A"] == []
        assert g.unresolved_refs["A"] == []


# ---------------------------------------------------------------------------
# DependencyGraph — requires edges
# ---------------------------------------------------------------------------


class TestDependencyGraphRequires:
    """add_requires() and edge-direction semantics."""

    def test_add_requires_creates_edge(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_requires("A", "B")  # A requires B

        assert g.edge_count == 1

    def test_edge_direction_prereq_to_dependent(self):
        """Internal edge is B -> A (prereq points to dependent)."""
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_requires("A", "B")

        # B -> A exists in the DiGraph
        assert g._graph.has_edge("B", "A")
        # A -> B does NOT exist
        assert not g._graph.has_edge("A", "B")

    def test_get_prerequisites(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_node(_make_node("C"))
        g.add_requires("A", "B")  # A requires B
        g.add_requires("A", "C")  # A requires C

        prereqs = g.get_prerequisites("A")
        assert set(prereqs) == {"B", "C"}

    def test_get_dependents(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_node(_make_node("C"))
        g.add_requires("B", "A")  # B requires A
        g.add_requires("C", "A")  # C requires A

        dependents = g.get_dependents("A")
        assert set(dependents) == {"B", "C"}

    def test_prerequisites_and_dependents_inverse(self):
        """get_prerequisites and get_dependents are inverses."""
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_requires("A", "B")

        assert "B" in g.get_prerequisites("A")
        assert "A" in g.get_dependents("B")

    def test_duplicate_requires_edge_no_double(self):
        """Adding the same requires edge twice should not create duplicate edges."""
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_requires("A", "B")
        g.add_requires("A", "B")

        assert g.edge_count == 1

    def test_add_requires_with_nonexistent_nodes(self):
        """Adding requires for nodes not added via add_node still works in nx.
        The graph implicitly creates the node ids, though they won't be in
        the nodes dict."""
        g = DependencyGraph()
        # No add_node calls
        g.add_requires("X", "Y")

        # Edge exists in the nx graph
        assert g._graph.has_edge("Y", "X")
        # But nodes dict does NOT contain them
        assert "X" not in g.nodes
        assert "Y" not in g.nodes

    def test_chain_a_requires_b_requires_c(self):
        """A -> B -> C chain: A requires B, B requires C."""
        g = DependencyGraph()
        for label in ("A", "B", "C"):
            g.add_node(_make_node(label))
        g.add_requires("A", "B")
        g.add_requires("B", "C")

        assert g.get_prerequisites("A") == ["B"]
        assert g.get_prerequisites("B") == ["C"]
        assert g.get_dependents("C") == ["B"]
        assert g.get_dependents("B") == ["A"]


# ---------------------------------------------------------------------------
# DependencyGraph — related edges
# ---------------------------------------------------------------------------


class TestDependencyGraphRelated:
    """add_related() and get_related()."""

    def test_add_related(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_related("A", "B")

        assert g.get_related("A") == ["B"]

    def test_related_is_not_symmetric_by_default(self):
        """add_related(A, B) does NOT imply add_related(B, A)."""
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_related("A", "B")

        assert g.get_related("A") == ["B"]
        assert g.get_related("B") == []

    def test_duplicate_related_no_double(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_related("A", "B")
        g.add_related("A", "B")

        assert g.get_related("A") == ["B"]

    def test_multiple_related(self):
        g = DependencyGraph()
        for label in ("A", "B", "C"):
            g.add_node(_make_node(label))
        g.add_related("A", "B")
        g.add_related("A", "C")

        assert set(g.get_related("A")) == {"B", "C"}

    def test_get_related_unknown_node(self):
        g = DependencyGraph()
        assert g.get_related("nonexistent") == []

    def test_related_does_not_affect_edge_count(self):
        """Related edges are stored separately — they are NOT in the nx DiGraph."""
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_node(_make_node("B"))
        g.add_related("A", "B")

        assert g.edge_count == 0


# ---------------------------------------------------------------------------
# DependencyGraph — isolated nodes (no edges)
# ---------------------------------------------------------------------------


class TestDependencyGraphIsolated:
    """Nodes with no edges / nonexistent nodes return empty lists."""

    @pytest.mark.parametrize(
        "method,node_id,add_node",
        [
            pytest.param("get_prerequisites", "A", True, id="isolated_prereqs"),
            pytest.param("get_dependents", "A", True, id="isolated_deps"),
            pytest.param("get_related", "A", True, id="isolated_related"),
            pytest.param("get_prerequisites", "ghost", False, id="nonexistent_prereqs"),
            pytest.param("get_dependents", "ghost", False, id="nonexistent_deps"),
        ],
    )
    def test_empty_list_returned(self, method, node_id, add_node):
        g = DependencyGraph()
        if add_node:
            g.add_node(_make_node(node_id))
        assert getattr(g, method)(node_id) == []


# ---------------------------------------------------------------------------
# DependencyGraph — unresolved refs
# ---------------------------------------------------------------------------


class TestDependencyGraphUnresolved:
    """add_unresolved() tracks references that couldn't be resolved."""

    def test_add_unresolved(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_unresolved("A", "missing_card_id")

        assert g.unresolved_refs["A"] == ["missing_card_id"]

    def test_add_unresolved_no_duplicates(self):
        g = DependencyGraph()
        g.add_node(_make_node("A"))
        g.add_unresolved("A", "ref1")
        g.add_unresolved("A", "ref1")

        assert g.unresolved_refs["A"] == ["ref1"]

    def test_add_unresolved_for_unknown_node(self):
        """Unresolved refs can be tracked even for node ids not in add_node."""
        g = DependencyGraph()
        g.add_unresolved("ghost", "ref1")

        assert g.unresolved_refs["ghost"] == ["ref1"]


# ---------------------------------------------------------------------------
# DependencyGraph — edge_count property
# ---------------------------------------------------------------------------


class TestDependencyGraphEdgeCount:
    def test_counts_requires_only(self):
        g = DependencyGraph()
        for label in ("A", "B", "C"):
            g.add_node(_make_node(label))
        g.add_requires("A", "B")
        g.add_requires("A", "C")
        g.add_related("B", "C")  # should NOT count

        assert g.edge_count == 2


# ---------------------------------------------------------------------------
# LocalGraphResult
# ---------------------------------------------------------------------------


class TestLocalGraphResult:
    """LocalGraphResult is a simple data container."""

    def test_local_graph_result_creation(self):
        center = _make_node("center")
        prereqs = [_make_node("p1")]
        deps = [_make_node("d1")]
        related = [_make_node("r1")]
        cycles = [["a", "b"]]

        result = LocalGraphResult(
            center=center,
            prerequisites=prereqs,
            dependents=deps,
            related=related,
            cycles=cycles,
        )

        assert result.center.id == "center"
        assert len(result.prerequisites) == 1
        assert len(result.dependents) == 1
        assert len(result.related) == 1
        assert result.cycles == [["a", "b"]]
