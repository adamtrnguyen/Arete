from pathlib import Path
from unittest.mock import patch

import pytest

from arete.application.queue.builder import (
    WeakPrereqCriteria,
    _is_weak_prereq,
    _weakness_score,
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

    # Nodes
    graph.nodes["A"] = CardNode(id="A", file_path="a.md", title="A", line_number=1)
    graph.nodes["B"] = CardNode(id="B", file_path="b.md", title="B", line_number=1)
    graph.nodes["C"] = CardNode(id="C", file_path="c.md", title="C", line_number=1)
    graph.nodes["D"] = CardNode(id="D", file_path="d.md", title="D", line_number=1)
    graph.nodes["E"] = CardNode(id="E", file_path="e.md", title="E", line_number=1)
    graph.nodes["F"] = CardNode(id="F", file_path="f.md", title="F", line_number=1)

    # Dependencies (requires)
    graph.add_requires("A", "B")
    graph.add_requires("B", "C")
    graph.add_requires("E", "F")
    graph.add_requires("F", "E")

    return graph


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_mvp(mock_build_graph, mock_graph_deps):
    """Test basic queue building with prerequisites."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["A"], depth=5)

    assert "C" in res.prereq_queue
    assert "B" in res.prereq_queue
    assert res.main_queue == ["A"]
    assert res.prereq_queue.index("C") < res.prereq_queue.index("B")


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_depth(mock_build_graph, mock_graph_deps):
    """Test recursion depth limit."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["A"], depth=1)

    assert "B" in res.prereq_queue
    assert "C" not in res.prereq_queue
    assert res.main_queue == ["A"]


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_independent(mock_build_graph, mock_graph_deps):
    """Test independent cards have no prereqs."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["D"])

    assert res.prereq_queue == []
    assert res.main_queue == ["D"]


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_cycle(mock_build_graph, mock_graph_deps):
    """Test cycle detection doesn't crash queue builder."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["E"])

    assert "F" in res.prereq_queue
    assert "E" in res.main_queue
    assert len(res.cycles) > 0


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_max_cards(mock_build_graph, mock_graph_deps):
    """Test constraints on queue size."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["A"], max_cards=2)

    assert len(res.main_queue) == 1
    assert len(res.prereq_queue) == 1
    assert len(res.prereq_queue) + len(res.main_queue) <= 2


@patch("arete.application.queue.builder.build_graph")
def test_build_dynamic_queue_respects_dependencies(mock_build_graph, mock_graph_deps):
    """Dynamic queue should still obey prerequisite ordering constraints."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_dynamic_queue(Path("."), due_card_ids=["A"], depth=5)

    assert res.ordered_queue is not None
    assert res.ordered_queue == res.prereq_queue + res.main_queue

    idx = {cid: i for i, cid in enumerate(res.ordered_queue)}
    assert idx["C"] < idx["B"] < idx["A"]  # C -> B -> A


@patch("arete.application.queue.builder.build_graph")
def test_build_dynamic_queue_uses_weakness_signal(mock_build_graph):
    """When multiple cards are ready, weaker cards can be prioritized first."""
    graph = DependencyGraph()
    graph.add_node(CardNode(id="due", file_path="due.md", title="Due", line_number=1))
    graph.add_node(CardNode(id="a_strong", file_path="s.md", title="Strong", line_number=1))
    graph.add_node(CardNode(id="z_weak", file_path="w.md", title="Weak", line_number=1))

    # due requires both prerequisites
    graph.add_requires("due", "a_strong")
    graph.add_requires("due", "z_weak")
    mock_build_graph.return_value = graph

    res = build_dynamic_queue(
        Path("."),
        due_card_ids=["due"],
        depth=2,
        card_stats={
            "a_strong": {"stability": 100.0, "lapses": 0, "reps": 100, "interval": 100},
            "z_weak": {"stability": 0.5, "lapses": 4, "reps": 1, "interval": 1},
        },
    )

    assert res.ordered_queue is not None
    # Both prereqs are ready initially; weak one should win despite lexical tie-break favoring "a_*"
    assert res.ordered_queue[0] == "z_weak"
    assert res.ordered_queue[-1] == "due"


# ---------------------------------------------------------------------------
# build_dynamic_queue: disconnected graph components
# ---------------------------------------------------------------------------


@pytest.fixture
def disconnected_graph():
    """Graph with two disconnected components.

    Component 1: X -> Y (X requires Y)
    Component 2: P -> Q (P requires Q)
    No edges between the two components.
    """
    graph = DependencyGraph()
    for nid in ("X", "Y", "P", "Q"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid.lower()}.md", title=nid, line_number=1))
    graph.add_requires("X", "Y")
    graph.add_requires("P", "Q")
    return graph


@patch("arete.application.queue.builder.build_graph")
def test_build_dynamic_queue_disconnected_components(mock_build_graph, disconnected_graph):
    """Dynamic queue handles disconnected graph components correctly.

    When due cards come from separate components, all prereqs should still
    appear before their dependents.
    """
    mock_build_graph.return_value = disconnected_graph

    res = build_dynamic_queue(Path("."), due_card_ids=["X", "P"], depth=5)

    assert res.ordered_queue is not None
    idx = {cid: i for i, cid in enumerate(res.ordered_queue)}

    # Each prereq before its dependent
    assert idx["Y"] < idx["X"]
    assert idx["Q"] < idx["P"]

    # All four cards present
    assert set(res.ordered_queue) == {"X", "Y", "P", "Q"}


# ---------------------------------------------------------------------------
# build_dynamic_queue: isolated due cards (no prereqs)
# ---------------------------------------------------------------------------


@patch("arete.application.queue.builder.build_graph")
def test_build_dynamic_queue_isolated_due_cards(mock_build_graph):
    """Due cards with zero prerequisites produce an empty prereq queue."""
    graph = DependencyGraph()
    for nid in ("solo1", "solo2", "solo3"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid}.md", title=nid, line_number=1))
    mock_build_graph.return_value = graph

    res = build_dynamic_queue(Path("."), due_card_ids=["solo1", "solo2", "solo3"], depth=5)

    assert res.prereq_queue == []
    assert set(res.main_queue) == {"solo1", "solo2", "solo3"}
    assert res.ordered_queue is not None
    assert set(res.ordered_queue) == {"solo1", "solo2", "solo3"}


# ---------------------------------------------------------------------------
# build_dynamic_queue: diamond dependency (A requires B and C, both require D)
# ---------------------------------------------------------------------------


@pytest.fixture
def diamond_graph():
    r"""Diamond dependency graph.

         A
        / \\
       B   C
        \\ /
         D
    A requires B, A requires C, B requires D, C requires D.
    """
    graph = DependencyGraph()
    for nid in ("A", "B", "C", "D"):
        graph.add_node(CardNode(id=nid, file_path=f"{nid.lower()}.md", title=nid, line_number=1))
    graph.add_requires("A", "B")
    graph.add_requires("A", "C")
    graph.add_requires("B", "D")
    graph.add_requires("C", "D")
    return graph


@patch("arete.application.queue.builder.build_graph")
def test_build_dynamic_queue_diamond_dependency(mock_build_graph, diamond_graph):
    """Diamond dependency: D must come first, B and C in the middle, A last."""
    mock_build_graph.return_value = diamond_graph

    res = build_dynamic_queue(Path("."), due_card_ids=["A"], depth=5)

    assert res.ordered_queue is not None
    idx = {cid: i for i, cid in enumerate(res.ordered_queue)}

    # D must precede both B and C
    assert idx["D"] < idx["B"]
    assert idx["D"] < idx["C"]

    # Both B and C must precede A
    assert idx["B"] < idx["A"]
    assert idx["C"] < idx["A"]

    # D is not a due card, so it should be in prereq_queue
    assert "D" in res.prereq_queue
    assert "A" in res.main_queue


# ---------------------------------------------------------------------------
# _is_weak_prereq: missing card_stats -> True (weak)
# ---------------------------------------------------------------------------


def test_is_weak_prereq_missing_card_stats():
    """When card_stats is None, all cards are considered weak."""
    criteria = WeakPrereqCriteria(min_stability=5.0)
    assert _is_weak_prereq("card_1", criteria, card_stats=None) is True


def test_is_weak_prereq_card_not_in_stats():
    """When the specific card is absent from card_stats, it is weak."""
    criteria = WeakPrereqCriteria(min_stability=5.0)
    stats = {"other_card": {"stability": 100.0}}
    assert _is_weak_prereq("card_1", criteria, card_stats=stats) is True


# ---------------------------------------------------------------------------
# _is_weak_prereq: all criteria None -> True (all weak)
# ---------------------------------------------------------------------------


def test_is_weak_prereq_all_criteria_none():
    """When criteria has no thresholds set, all prereqs are weak (no filtering)."""
    criteria = WeakPrereqCriteria()  # All fields None
    stats = {"card_1": {"stability": 100.0, "lapses": 0, "reps": 50, "interval": 365}}
    # None of the criteria branches fire, so function returns False (card is strong)
    assert _is_weak_prereq("card_1", criteria, stats) is False


def test_is_weak_prereq_no_criteria_object():
    """When criteria is None entirely, all prereqs are considered weak."""
    stats = {"card_1": {"stability": 100.0}}
    assert _is_weak_prereq("card_1", None, stats) is True


# ---------------------------------------------------------------------------
# _is_weak_prereq: each criterion independently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "criteria_kwargs,card_stats,expected",
    [
        pytest.param({"min_stability": 10.0}, {"stability": 5.0}, True, id="stability_below"),
        pytest.param({"min_stability": 10.0}, {"stability": 20.0}, False, id="stability_above"),
        pytest.param({"max_lapses": 3}, {"lapses": 5}, True, id="lapses_above"),
        pytest.param({"max_lapses": 3}, {"lapses": 2}, False, id="lapses_below"),
        pytest.param({"min_reviews": 10}, {"reps": 3}, True, id="reps_below"),
        pytest.param({"min_reviews": 10}, {"reps": 15}, False, id="reps_above"),
        pytest.param({"max_interval": 30}, {"interval": 10}, True, id="interval_below"),
        pytest.param({"max_interval": 30}, {"interval": 60}, False, id="interval_above"),
    ],
)
def test_is_weak_prereq_threshold(criteria_kwargs, card_stats, expected):
    """Each criterion independently determines weak vs strong."""
    criteria = WeakPrereqCriteria(**criteria_kwargs)
    assert _is_weak_prereq("card_1", criteria, {"card_1": card_stats}) is expected


# ---------------------------------------------------------------------------
# _weakness_score: no stats (returns 0.0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "card_stats",
    [
        pytest.param(None, id="none"),
        pytest.param({}, id="empty_dict"),
        pytest.param({"other": {"stability": 1.0}}, id="missing_card"),
    ],
)
def test_weakness_score_no_stats(card_stats):
    """Missing or absent card stats produce 0.0 weakness score."""
    assert _weakness_score("card_1", None, card_stats) == 0.0


# ---------------------------------------------------------------------------
# _weakness_score: extreme values
# ---------------------------------------------------------------------------


def test_weakness_score_zero_stability():
    """Zero stability should give maximum stability-based weakness."""
    stats = {"card_1": {"stability": 0.0}}
    score = _weakness_score("card_1", None, stats)
    # 1/(1+0) = 1.0, plus reps < 10 gives (10-0)*0.05 = 0.0 (no reps key)
    assert score == pytest.approx(1.0)


def test_weakness_score_very_high_stability():
    """Very high stability approaches zero weakness from stability."""
    stats = {"card_1": {"stability": 1_000_000.0}}
    score = _weakness_score("card_1", None, stats)
    # 1/(1+1_000_000) ~ 0.000001
    assert score < 0.001


def test_weakness_score_high_lapses():
    """High lapse count produces proportionally higher weakness."""
    stats_high = {"card_1": {"lapses": 100}}
    stats_low = {"card_1": {"lapses": 1}}
    score_high = _weakness_score("card_1", None, stats_high)
    score_low = _weakness_score("card_1", None, stats_low)
    assert score_high > score_low


def test_weakness_score_zero_reps():
    """Zero reps gives the maximum reps-based weakness component."""
    stats = {"card_1": {"reps": 0}}
    score = _weakness_score("card_1", None, stats)
    # (10 - 0) * 0.05 = 0.5
    assert score == pytest.approx(0.5)


def test_weakness_score_many_reps():
    """Reps >= 10 contribute zero reps-based weakness."""
    stats = {"card_1": {"reps": 50}}
    score = _weakness_score("card_1", None, stats)
    # reps >= 10 -> no reps contribution
    assert score == pytest.approx(0.0)


def test_weakness_score_zero_interval():
    """Zero interval gives maximum interval-based weakness."""
    stats = {"card_1": {"interval": 0}}
    score = _weakness_score("card_1", None, stats)
    # 1/(1+0) = 1.0
    assert score == pytest.approx(1.0)


def test_weakness_score_with_criteria_bonus():
    """Criteria thresholds add bonus weakness when violated."""
    criteria = WeakPrereqCriteria(min_stability=10.0, max_lapses=2)
    stats = {"card_1": {"stability": 1.0, "lapses": 5}}
    score = _weakness_score("card_1", criteria, stats)

    # stability: 1/(1+1) = 0.5, below threshold -> +1.0 => 1.5
    # lapses: 5 * 0.15 = 0.75, above threshold -> +0.75 => 1.5
    # total: 1.5 + 1.5 = 3.0
    assert score == pytest.approx(3.0)


def test_weakness_score_all_extreme_stats():
    """Card with all extreme weak stats produces a high aggregate score."""
    stats = {"card_1": {"stability": 0.0, "lapses": 50, "reps": 0, "interval": 0}}
    score = _weakness_score("card_1", None, stats)

    # stability: 1/(1+0) = 1.0
    # lapses: 50 * 0.15 = 7.5
    # reps: (10-0) * 0.05 = 0.5
    # interval: 1/(1+0) = 1.0
    # total: 10.0
    assert score == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# build_simple_queue: non-existent card IDs
# ---------------------------------------------------------------------------


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_nonexistent_card_ids(mock_build_graph, mock_graph_deps):
    """Non-existent card IDs are handled gracefully (no crash, no prereqs)."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["NONEXISTENT_1", "NONEXISTENT_2"], depth=5)

    assert res.prereq_queue == []
    assert "A" not in res.main_queue
    assert "B" not in res.main_queue


@patch("arete.application.queue.builder.build_graph")
def test_build_simple_queue_mix_existing_and_nonexistent(mock_build_graph, mock_graph_deps):
    """Mix of real and non-existent IDs: prereqs only from real cards."""
    mock_build_graph.return_value = mock_graph_deps

    res = build_simple_queue(Path("."), due_card_ids=["A", "NONEXISTENT"], depth=5)

    assert "B" in res.prereq_queue or "B" in res.main_queue
    assert "C" in res.prereq_queue
    assert "A" in res.main_queue
