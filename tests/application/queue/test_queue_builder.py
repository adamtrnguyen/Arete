"""Unit tests for queue builder using direct DependencyGraph injection."""

from pathlib import Path

import pytest

from arete.application.queue.builder import (
    build_dynamic_queue,
    build_simple_queue,
)
from arete.domain.graph import CardNode, DependencyGraph


@pytest.fixture
def mock_graph_deps():
    """Create a dependency graph.

    A -> B (A requires B)
    B -> C (B requires C)
    D (independent)
    E <-> F (Cycle).
    """
    graph = DependencyGraph()
    for nid in ("A", "B", "C", "D", "E", "F"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid.lower()}.md", title=nid, line_number=1))
    graph.add_requires("A", "B")
    graph.add_requires("B", "C")
    graph.add_requires("E", "F")
    graph.add_requires("F", "E")
    return graph


def test_build_simple_queue_mvp(mock_graph_deps):
    """Test basic queue building with prerequisites."""
    res = build_simple_queue(Path("."), due_card_ids=["A"], depth=5, graph=mock_graph_deps)

    assert "C" in res.prereq_queue
    assert "B" in res.prereq_queue
    assert res.main_queue == ["A"]
    assert res.prereq_queue.index("C") < res.prereq_queue.index("B")


def test_build_simple_queue_depth(mock_graph_deps):
    """Test recursion depth limit."""
    res = build_simple_queue(Path("."), due_card_ids=["A"], depth=1, graph=mock_graph_deps)

    assert "B" in res.prereq_queue
    assert "C" not in res.prereq_queue
    assert res.main_queue == ["A"]


def test_build_simple_queue_independent(mock_graph_deps):
    """Test independent cards have no prereqs."""
    res = build_simple_queue(Path("."), due_card_ids=["D"], graph=mock_graph_deps)

    assert res.prereq_queue == []
    assert res.main_queue == ["D"]


def test_build_simple_queue_cycle(mock_graph_deps):
    """Test cycle detection doesn't crash queue builder."""
    res = build_simple_queue(Path("."), due_card_ids=["E"], graph=mock_graph_deps)

    assert "F" in res.prereq_queue
    assert "E" in res.main_queue
    assert len(res.cycles) > 0


def test_build_simple_queue_max_cards(mock_graph_deps):
    """Test constraints on queue size."""
    res = build_simple_queue(Path("."), due_card_ids=["A"], max_cards=2, graph=mock_graph_deps)

    assert len(res.main_queue) == 1
    assert len(res.prereq_queue) == 1
    assert len(res.prereq_queue) + len(res.main_queue) <= 2


def test_build_dynamic_queue_respects_dependencies(mock_graph_deps):
    """Dynamic queue should still obey prerequisite ordering constraints."""
    res = build_dynamic_queue(Path("."), due_card_ids=["A"], depth=5, graph=mock_graph_deps)

    assert res.ordered_queue is not None
    assert res.ordered_queue == res.prereq_queue + res.main_queue

    idx = {cid: i for i, cid in enumerate(res.ordered_queue)}
    assert idx["C"] < idx["B"] < idx["A"]  # C -> B -> A


@pytest.fixture
def disconnected_graph():
    """Graph with two disconnected components: X -> Y and P -> Q."""
    graph = DependencyGraph()
    for nid in ("X", "Y", "P", "Q"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid.lower()}.md", title=nid, line_number=1))
    graph.add_requires("X", "Y")
    graph.add_requires("P", "Q")
    return graph


def test_build_dynamic_queue_disconnected_components(disconnected_graph):
    """Dynamic queue handles disconnected graph components correctly."""
    res = build_dynamic_queue(Path("."), due_card_ids=["X", "P"], depth=5, graph=disconnected_graph)

    assert res.ordered_queue is not None
    idx = {cid: i for i, cid in enumerate(res.ordered_queue)}
    assert idx["Y"] < idx["X"]
    assert idx["Q"] < idx["P"]
    assert set(res.ordered_queue) == {"X", "Y", "P", "Q"}


def test_build_dynamic_queue_isolated_due_cards():
    """Due cards with zero prerequisites produce an empty prereq queue."""
    graph = DependencyGraph()
    for nid in ("solo1", "solo2", "solo3"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid}.md", title=nid, line_number=1))

    res = build_dynamic_queue(
        Path("."), due_card_ids=["solo1", "solo2", "solo3"], depth=5, graph=graph
    )

    assert res.prereq_queue == []
    assert set(res.main_queue) == {"solo1", "solo2", "solo3"}
    assert res.ordered_queue is not None
    assert set(res.ordered_queue) == {"solo1", "solo2", "solo3"}


@pytest.fixture
def diamond_graph():
    """Diamond dependency graph: A requires B and C, both require D."""
    graph = DependencyGraph()
    for nid in ("A", "B", "C", "D"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid.lower()}.md", title=nid, line_number=1))
    graph.add_requires("A", "B")
    graph.add_requires("A", "C")
    graph.add_requires("B", "D")
    graph.add_requires("C", "D")
    return graph


def test_build_dynamic_queue_diamond_dependency(diamond_graph):
    """Diamond dependency: D must come first, B and C in the middle, A last."""
    res = build_dynamic_queue(Path("."), due_card_ids=["A"], depth=5, graph=diamond_graph)

    assert res.ordered_queue is not None
    idx = {cid: i for i, cid in enumerate(res.ordered_queue)}
    assert idx["D"] < idx["B"]
    assert idx["D"] < idx["C"]
    assert idx["B"] < idx["A"]
    assert idx["C"] < idx["A"]
    assert "D" in res.prereq_queue
    assert "A" in res.main_queue


def test_build_simple_queue_nonexistent_card_ids(mock_graph_deps):
    """Non-existent card IDs are handled gracefully (no crash, no prereqs)."""
    res = build_simple_queue(
        Path("."),
        due_card_ids=["NONEXISTENT_1", "NONEXISTENT_2"],
        depth=5,
        graph=mock_graph_deps,
    )

    assert res.prereq_queue == []
    assert "A" not in res.main_queue
    assert "B" not in res.main_queue


def test_build_simple_queue_mix_existing_and_nonexistent(mock_graph_deps):
    """Mix of real and non-existent IDs: prereqs only from real cards."""
    res = build_simple_queue(
        Path("."), due_card_ids=["A", "NONEXISTENT"], depth=5, graph=mock_graph_deps
    )

    assert "B" in res.prereq_queue or "B" in res.main_queue
    assert "C" in res.prereq_queue
    assert "A" in res.main_queue


def _chain_graph(edges, nodes):
    graph = DependencyGraph()
    for n in nodes:
        graph.add_node(CardNode(id=n, file_path=f"{n}.md", title=n, line_number=1))
    for dependent, prereq in edges:
        graph.add_requires(dependent, prereq)
    return graph


def test_prereq_within_depth_is_found_by_its_shortest_path():
    """A card reached first by a long path is expanded from its shortest one."""
    graph = _chain_graph([("D", "A"), ("D", "B"), ("A", "B"), ("B", "C"), ("C", "E")], "ABCDE")
    res = build_simple_queue(Path("."), due_card_ids=["D"], depth=3, max_cards=50, graph=graph)
    assert set(res.prereq_queue) == {"A", "B", "C", "E"}


def test_max_cards_keeps_the_nearest_prereqs_and_reports_what_it_dropped():
    """The cap keeps the nearest prerequisites, not the alphabetically first, and reports the rest."""
    graph = _chain_graph([("Due", "Z"), ("Z", "B"), ("B", "A")], ["Due", "Z", "B", "A"])
    res = build_simple_queue(Path("."), due_card_ids=["Due"], depth=5, max_cards=3, graph=graph)
    assert set(res.prereq_queue) == {"Z", "B"}
    assert res.dropped_prereqs == ["A"]


def test_dynamic_queue_preserves_dropped_prereqs_and_case_insensitive_due_ids():
    """Dynamic queue preserves dropped_prereqs and deduplicates case-folded due IDs for room."""
    graph = _chain_graph(
        [("arete_Due", "arete_Z"), ("arete_Z", "arete_B"), ("arete_B", "arete_A")],
        ["arete_Due", "arete_Z", "arete_B", "arete_A"],
    )
    res = build_dynamic_queue(
        Path("."), due_card_ids=["arete_due", "arete_DUE"], depth=5, max_cards=3, graph=graph
    )
    assert res.main_queue == ["arete_Due"]
    assert set(res.prereq_queue) == {"arete_Z", "arete_B"}
    assert res.dropped_prereqs == ["arete_A"]
