"""Tests for graph resolver and queue builder."""

from pathlib import Path

import pytest

from arete.application.queue.graph_resolver import (
    build_graph,
    detect_cycles,
    find_connected_components,
    find_isolated_nodes,
    get_local_graph,
    topological_sort,
)
from arete.application.queue.builder import (
    WeakPrereqCriteria,
    build_dependency_queue,
)
from arete.domain.graph import CardNode, DependencyGraph


class TestDependencyGraph:
    """Tests for DependencyGraph domain model."""

    def test_add_node(self):
        graph = DependencyGraph()
        node = CardNode(id="a1", title="Card A", file_path="/test.md", line_number=1)
        graph.add_node(node)

        assert "a1" in graph.nodes
        assert graph.nodes["a1"].title == "Card A"

    def test_add_requires(self):
        graph = DependencyGraph()
        graph.add_node(CardNode("a1", "A", "/a.md", 1))
        graph.add_node(CardNode("a2", "B", "/b.md", 1))
        graph.add_requires("a1", "a2")  # a1 requires a2

        assert graph.get_prerequisites("a1") == ["a2"]
        assert graph.get_dependents("a2") == ["a1"]

    def test_add_requires_new_id(self):
        """Test add_requires with an ID that wasn't previously added via add_node."""
        graph = DependencyGraph()
        graph.add_requires("parent", "child")
        assert "child" in graph.get_prerequisites("parent")

    def test_add_related_new_id(self):
        """Test add_related with an ID that wasn't previously added via add_node."""
        graph = DependencyGraph()
        graph.add_related("a", "b")
        assert "b" in graph.get_related("a")


class TestBuildGraph:
    """Tests for building graph from vault files."""

    def test_build_graph_from_yaml(self, tmp_path: Path):
        """Test parsing cards with deps from frontmatter."""
        md_content = """---
arete: true
deck: Test
cards:
  - id: arete_001
    model: Basic
    fields:
      Front: "Question 1"
      Back: "Answer 1"
    deps:
      requires: [arete_002]
      related: [arete_003]
  - id: arete_002
    model: Basic
    fields:
      Front: "Question 2"
      Back: "Answer 2"
  - id: arete_003
    model: Basic
    fields:
      Front: "Question 3"
      Back: "Answer 3"
---

# Test Note
"""
        (tmp_path / "test.md").write_text(md_content)

        graph = build_graph(tmp_path)

        assert "arete_001" in graph.nodes
        assert "arete_002" in graph.nodes
        assert "arete_003" in graph.nodes
        assert graph.get_prerequisites("arete_001") == ["arete_002"]
        assert graph.get_related("arete_001") == ["arete_003"]

    def test_build_graph_skips_cards_without_id(self, tmp_path: Path):
        """Test that cards without id field are skipped."""
        md_content = """---
arete: true
deck: Test
cards:
  - model: Basic
    fields:
      Front: "No ID"
      Back: "Skipped"
---
"""
        (tmp_path / "test.md").write_text(md_content)

        graph = build_graph(tmp_path)

        assert len(graph.nodes) == 0

    def test_build_graph_invalid_frontmatter(self, tmp_path: Path, caplog):
        """Test handling of invalid frontmatter or missing cards field."""
        # Case 1: YAML error
        (tmp_path / "error.md").write_text("---\ninvalid: [unclosed\n---\n")
        # Case 2: cards is not a list
        (tmp_path / "not_list.md").write_text("---\narete: true\ncards: not-a-list\n---\n")
        # Case 3: card is not a dict
        (tmp_path / "card_not_dict.md").write_text("---\narete: true\ncards:\n  - not_a_dict\n---\n")
        # Case 4: card fields is not a dict (tests line 53)
        (tmp_path / "fields_not_dict.md").write_text(
            "---\narete: true\ncards:\n  - id: c1\n    fields: string\n---\n"
        )

        with caplog.at_level("WARNING"):
            graph = build_graph(tmp_path)

        assert "c1" in graph.nodes
        assert graph.nodes["c1"].title == "c1"  # Fallback to card_id


class TestLocalGraph:
    """Tests for local graph queries."""

    def test_get_local_graph(self):
        """Test local subgraph extraction."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")  # a requires b
        graph.add_requires("b", "c")  # b requires c

        result = get_local_graph(graph, "a", depth=2)

        assert result is not None
        assert result.center.id == "a"
        assert len(result.prerequisites) == 2  # b and c
        prereq_ids = {p.id for p in result.prerequisites}
        assert "b" in prereq_ids
        assert "c" in prereq_ids

    def test_get_local_graph_depth_limit(self):
        """Test that depth limit is respected."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("b", "c")

        result = get_local_graph(graph, "a", depth=1)

        assert result is not None
        assert len(result.prerequisites) == 1  # Only b, not c
        assert result.prerequisites[0].id == "b"

    def test_get_local_graph_not_found(self):
        """Test handling of non-existent card."""
        graph = DependencyGraph()
        result = get_local_graph(graph, "nonexistent")
        assert result is None

    def test_get_local_graph_with_dependents_and_related(self):
        """Test local graph including dependents and existing/non-existing related cards."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("b", "a")  # b depends on a (a is prereq of b)
        graph.add_requires("a", "c")  # a depends on c (c is prereq of a)
        graph.add_related("a", "b")
        graph.add_related("a", "nonexistent")

        result = get_local_graph(graph, "a", depth=1)

        assert result.center.id == "a"
        assert len(result.dependents) == 1
        assert result.dependents[0].id == "b"
        assert len(result.prerequisites) == 1
        assert result.prerequisites[0].id == "c"
        assert len(result.related) == 1
        assert result.related[0].id == "b"

    def test_get_local_graph_limits(self):
        """Test max_nodes limit in local graph traversal."""
        graph = DependencyGraph()
        graph.add_node(CardNode("center", "Center", "/c.md", 1))
        for i in range(10):
            node_id = f"node_{i}"
            graph.add_node(CardNode(node_id, node_id, "/file.md", 1))
            graph.add_requires("center", node_id)

        # Test max_nodes = 5
        result = get_local_graph(graph, "center", depth=1, max_nodes=5)
        assert len(result.prerequisites) == 5


class TestCycleDetection:
    """Tests for cycle detection."""

    def test_detect_no_cycles(self):
        """Test graph with no cycles."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_requires("a", "b")

        cycles = detect_cycles(graph)
        assert len(cycles) == 0

    def test_detect_simple_cycle(self):
        """Test detection of a simple cycle."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("b", "a")

        cycles = detect_cycles(graph)
        assert len(cycles) > 0
        assert "a" in cycles[0]
        assert "b" in cycles[0]

    def test_detect_complex_cycle_for_card(self):
        """Test cycle detection relative to a card with missing nodes in path."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("b", "c")
        graph.add_requires("c", "a")
        graph.add_requires("a", "nonexistent")

        # cycles for 'a'
        from arete.application.queue.graph_resolver import detect_cycles_for_card

        cycles = detect_cycles_for_card(graph, "a")
        assert len(cycles) == 1
        assert sorted(cycles[0]) == ["a", "b", "c"]

        # Cycle for card not in graph
        assert detect_cycles_for_card(graph, "missing") == []


class TestTopologicalSort:
    """Tests for topological sorting."""

    def test_topological_sort_basic(self):
        """Test basic topological sort."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")  # a requires b
        graph.add_requires("b", "c")  # b requires c

        result = topological_sort(graph, ["a", "b", "c"])

        # c should come before b, b before a
        assert result.index("c") < result.index("b")
        assert result.index("b") < result.index("a")

    def test_topological_sort_subset(self):
        """Test sorting a subset of cards."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("b", "c")

        result = topological_sort(graph, ["a", "b"])  # Exclude c

        assert "c" not in result
        assert result.index("b") < result.index("a")

    def test_topological_sort_with_cycle(self, caplog):
        """Test fallback when cycle exists."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("b", "a")

        with caplog.at_level("WARNING"):
            result = topological_sort(graph, ["a", "b"])

        assert "Cycle detected" in caplog.text
        assert set(result) == {"a", "b"}


class TestFindIsolatedNodes:
    """Tests for find_isolated_nodes."""

    def test_find_isolated_nodes(self):
        """Graph with one connected pair + one isolated card."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")  # a requires b (both connected)

        isolated = find_isolated_nodes(graph)
        assert isolated == ["c"]

    def test_find_isolated_none(self):
        """Fully connected graph returns empty."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_requires("a", "b")

        isolated = find_isolated_nodes(graph)
        assert isolated == []


class TestFindConnectedComponents:
    """Tests for find_connected_components."""

    def test_single_component(self):
        """One connected component."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("c", "C", "/c.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("b", "c")

        components = find_connected_components(graph)
        assert len(components) == 1
        assert components[0] == {"a", "b", "c"}

    def test_multiple_components(self):
        """Two disconnected subgraphs."""
        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_node(CardNode("b", "B", "/b.md", 1))
        graph.add_node(CardNode("x", "X", "/x.md", 1))
        graph.add_node(CardNode("y", "Y", "/y.md", 1))
        graph.add_requires("a", "b")
        graph.add_requires("x", "y")

        components = find_connected_components(graph)
        assert len(components) == 2
        component_sets = [comp for comp in components]
        assert {"a", "b"} in component_sets
        assert {"x", "y"} in component_sets


class TestQueueBuilder:
    """Tests for dependency-aware queue building."""

    def test_build_dependency_queue(self, tmp_path: Path):
        """Test full queue building flow."""
        md_content = """---
arete: true
deck: Test
cards:
  - id: arete_main
    model: Basic
    fields:
      Front: "Main card"
      Back: "Due today"
    deps:
      requires: [arete_prereq]
  - id: arete_prereq
    model: Basic
    fields:
      Front: "Prereq card"
      Back: "Should study first"
---
"""
        (tmp_path / "test.md").write_text(md_content)

        result = build_dependency_queue(
            vault_root=tmp_path,
            due_card_ids=["arete_main"],
            depth=2,
        )

        assert "arete_prereq" in result.prereq_queue
        assert "arete_main" in result.main_queue

    def test_include_related_not_implemented(self, tmp_path: Path):
        """Test that include_related raises NotImplementedError."""
        (tmp_path / "test.md").write_text("---\narete: true\ncards: []\n---")

        with pytest.raises(NotImplementedError, match="Related card boost"):
            build_dependency_queue(
                vault_root=tmp_path,
                due_card_ids=[],
                include_related=True,
            )

    def test_weak_prereq_filtering(self, tmp_path: Path):
        """Test filtering based on weak criteria."""
        md_content = """---
arete: true
deck: Test
cards:
  - id: arete_main
    model: Basic
    fields:
      Front: "Main"
    deps:
      requires: [arete_weak, arete_strong]
  - id: arete_weak
    model: Basic
    fields:
      Front: "Weak prereq"
  - id: arete_strong
    model: Basic
    fields:
      Front: "Strong prereq"
---
"""
        (tmp_path / "test.md").write_text(md_content)

        card_stats = {
            "arete_weak": {"stability": 5.0, "lapses": 3},
            "arete_strong": {"stability": 100.0, "lapses": 0},
        }

        result = build_dependency_queue(
            vault_root=tmp_path,
            due_card_ids=["arete_main"],
            weak_criteria=WeakPrereqCriteria(min_stability=50.0),
            card_stats=card_stats,
        )

        assert "arete_weak" in result.prereq_queue
        assert "arete_strong" in result.skipped_strong

    def test_missing_prereqs(self, tmp_path: Path):
        """Test handling of prerequisites not found in vault."""
        md_content = """---
arete: true
cards:
  - id: arete_main
    deps:
      requires: [arete_missing_1, arete_missing_2]
---
"""
        (tmp_path / "test.md").write_text(md_content)
        result = build_dependency_queue(tmp_path, ["arete_main"])
        assert "arete_missing_1" in result.missing_prereqs
        assert "arete_missing_2" in result.missing_prereqs

    def test_max_nodes_capping_with_stats(self, tmp_path: Path):
        """Test that we cap the queue and sort by stability."""
        md_content = """---
arete: true
cards:
  - id: arete_main
    deps:
      requires: [arete_p1, arete_p2, arete_p3]
  - id: arete_p1
  - id: arete_p2
  - id: arete_p3
---
"""
        (tmp_path / "test.md").write_text(md_content)
        card_stats = {
            "arete_p1": {"stability": 10.0},
            "arete_p2": {"stability": 5.0},
            "arete_p3": {"stability": 20.0},
        }
        # Cap at 2 nodes
        result = build_dependency_queue(
            tmp_path, ["arete_main"], max_nodes=2, card_stats=card_stats
        )
        assert len(result.prereq_queue) == 2
        # p2 (5.0) and p1 (10.0) should be included as they are "weaker"
        assert "arete_p2" in result.prereq_queue
        assert "arete_p1" in result.prereq_queue
        assert "arete_p3" not in result.prereq_queue

    def test_is_weak_prereq_various_criteria(self):
        """Test all branches of _is_weak_prereq."""
        from arete.application.queue.builder import _is_weak_prereq

        # No criteria -> always weak
        assert _is_weak_prereq("any", None, None) is True

        # No stats -> assume weak
        criteria = WeakPrereqCriteria(min_stability=50.0)
        assert _is_weak_prereq("any", criteria, None) is True
        assert _is_weak_prereq("missing", criteria, {"other": {}}) is True

        # Lapses
        criteria = WeakPrereqCriteria(max_lapses=2)
        assert _is_weak_prereq("c", criteria, {"c": {"lapses": 3}}) is True
        assert _is_weak_prereq("c", criteria, {"c": {"lapses": 1}}) is False

        # Reviews (reps)
        criteria = WeakPrereqCriteria(min_reviews=5)
        assert _is_weak_prereq("c", criteria, {"c": {"reps": 3}}) is True
        assert _is_weak_prereq("c", criteria, {"c": {"reps": 10}}) is False

        # Interval
        criteria = WeakPrereqCriteria(max_interval=30)
        assert _is_weak_prereq("c", criteria, {"c": {"interval": 10}}) is True
        assert _is_weak_prereq("c", criteria, {"c": {"interval": 50}}) is False

    def test_collect_prereqs_cycles(self):
        """Test recursion protection in _collect_prereqs."""
        from arete.application.queue.builder import _collect_prereqs
        from arete.domain.graph import CardNode, DependencyGraph

        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_requires("a", "a")  # Self cycle

        visited = set()
        result = _collect_prereqs(graph, "a", depth=5, visited=visited)
        assert "a" in result
