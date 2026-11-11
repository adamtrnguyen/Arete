"""Tests for graph resolver and queue builder."""

import pathlib
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arete.application.queue.builder import build_simple_queue
from arete.application.queue.graph_resolver import (
    build_graph,
    check_graph_health,
    detect_cycles,
    filter_graph_by_deck,
    find_connected_components,
    find_isolated_nodes,
    topological_sort,
    vault_relative,
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
        """Test add_requires with an ID that add_node never saw."""
        graph = DependencyGraph()
        graph.add_requires("parent", "child")
        assert "child" in graph.get_prerequisites("parent")

    def test_add_related_new_id(self):
        """Test add_related with an ID that add_node never saw."""
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
        (tmp_path / "card_not_dict.md").write_text(
            "---\narete: true\ncards:\n  - not_a_dict\n---\n"
        )
        # Case 4: card fields is not a dict (tests line 53)
        (tmp_path / "fields_not_dict.md").write_text(
            "---\narete: true\ncards:\n  - id: c1\n    fields: string\n---\n"
        )

        with caplog.at_level("WARNING"):
            graph = build_graph(tmp_path)

        assert "c1" in graph.nodes
        assert graph.nodes["c1"].title == "c1"  # Fallback to card_id

    def test_build_graph_resolves_nfd_filename_with_nfc_ref(self, tmp_path: Path):
        """The dep resolver must normalize both sides of a filename comparison.

        macOS stores filenames decomposed (NFD). YAML a person types is composed
        (NFC). The two render identically and compare unequal byte for byte, so a
        ref to an accented filename such as 'Cramer-Rao Lower Bound' only resolves
        when both sides are normalized first.
        """
        import unicodedata

        # Create the target file with an NFD-encoded filename (simulates how
        # macOS pathlib hands back filenames after the OS stores them).
        target_name_nfd = unicodedata.normalize("NFD", "Cramér-Rao Lower Bound")
        target_md = """---
arete: true
deck: Test
cards:
  - id: arete_target
    model: Basic
    fields:
      Front: "What is the CRLB?"
      Back: "A lower bound on variance of an unbiased estimator."
---
"""
        (tmp_path / f"{target_name_nfd}.md").write_text(target_md, encoding="utf-8")

        # Create the dependent file with an NFC-encoded ref (how the YAML
        # loader hands back user-typed accented strings).
        ref_nfc = unicodedata.normalize("NFC", "Cramér-Rao Lower Bound")
        dependent_md = f"""---
arete: true
deck: Test
cards:
  - id: arete_dependent
    model: Basic
    fields:
      Front: "What is UMVUE?"
      Back: "An unbiased estimator achieving the CRLB."
    deps:
      requires: ["{ref_nfc}"]
---
"""
        (tmp_path / "UMVUE.md").write_text(dependent_md, encoding="utf-8")

        # The two forms must be byte-distinct, otherwise the test proves nothing.
        assert target_name_nfd != ref_nfc, "NFD and NFC must differ for this test to be meaningful"

        graph = build_graph(tmp_path)

        # The dep should resolve cleanly — no unresolved refs anywhere.
        # (unresolved_refs is pre-populated with empty lists per card; we check
        # that every list is empty.)
        unresolved_total = sum(len(refs) for refs in graph.unresolved_refs.values())
        assert unresolved_total == 0, (
            f"NFC-typed ref '{ref_nfc}' did not match NFD-stored "
            f"basename '{target_name_nfd}'. Got unresolved: {graph.unresolved_refs}"
        )
        assert "arete_target" in graph.get_prerequisites("arete_dependent")


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
        component_sets = list(components)
        assert {"a", "b"} in component_sets
        assert {"x", "y"} in component_sets


class TestQueueBuilder:
    """Tests for dependency-aware queue building."""

    def test_build_queue_pulls_in_prerequisites(self, tmp_path: Path):
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

        result = build_simple_queue(
            vault_root=tmp_path,
            due_card_ids=["arete_main"],
            depth=2,
        )

        assert "arete_prereq" in result.prereq_queue
        assert "arete_main" in result.main_queue

    def test_max_cards_caps_the_whole_queue(self, tmp_path: Path):
        """max_cards bounds due cards + prerequisites together, deterministically."""
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

        result = build_simple_queue(tmp_path, ["arete_main"], max_cards=3)

        assert len(result.prereq_queue) + len(result.main_queue) == 3
        # sorted() truncation, so the survivors do not depend on set iteration order
        assert result.prereq_queue == ["arete_p1", "arete_p2"]

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
        result = build_simple_queue(tmp_path, ["arete_main"])
        assert "arete_missing_1" in result.missing_prereqs
        assert "arete_missing_2" in result.missing_prereqs

    def test_collect_prereqs_cycles(self):
        """Test recursion protection in _collect_prereqs."""
        from arete.application.queue.builder import _collect_prereqs
        from arete.domain.graph import CardNode, DependencyGraph

        graph = DependencyGraph()
        graph.add_node(CardNode("a", "A", "/a.md", 1))
        graph.add_requires("a", "a")  # Self cycle

        # terminates, and a card is never its own prerequisite (the builder dropped it anyway)
        assert _collect_prereqs(graph, "a", depth=5) == {}


# ---------------------------------------------------------------------------
# check_graph_health
# ---------------------------------------------------------------------------


class TestCheckGraphHealth:
    """Tests for the check_graph_health high-level analysis function."""

    def _make_vault(self, tmp_path: Path, cards_yaml: str) -> Path:
        md = f"---\narete: true\ndeck: TestDeck\ncards:\n{cards_yaml}---\n"
        (tmp_path / "test.md").write_text(md)
        return tmp_path

    def test_healthy_graph(self, tmp_path: Path):
        self._make_vault(
            tmp_path,
            "  - id: arete_A\n    Front: A\n    deps:\n      requires: [arete_B]\n"
            "  - id: arete_B\n    Front: B\n",
        )
        result = check_graph_health(tmp_path)
        assert result.ok is True
        assert result.total_nodes == 2
        assert result.total_edges == 1
        assert len(result.cycles) == 0
        assert len(result.unresolved_refs) == 0

    def test_cycle_detected(self, tmp_path: Path):
        self._make_vault(
            tmp_path,
            "  - id: arete_A\n    Front: A\n    deps:\n      requires: [arete_B]\n"
            "  - id: arete_B\n    Front: B\n    deps:\n      requires: [arete_A]\n",
        )
        result = check_graph_health(tmp_path)
        assert result.ok is False
        assert len(result.cycles) == 1
        cycle_ids = {e.card_id for e in result.cycles[0]}
        assert cycle_ids == {"arete_A", "arete_B"}

    def test_unresolved_refs(self, tmp_path: Path):
        self._make_vault(
            tmp_path,
            "  - id: arete_A\n    Front: A\n    deps:\n      requires: [arete_MISSING]\n",
        )
        result = check_graph_health(tmp_path)
        assert result.ok is False
        assert len(result.unresolved_refs) == 1
        assert result.unresolved_refs[0].card_id == "arete_A"
        assert "arete_MISSING" in result.unresolved_refs[0].missing_refs

    def test_isolated_nodes(self, tmp_path: Path):
        self._make_vault(
            tmp_path,
            "  - id: arete_A\n    Front: A\n  - id: arete_B\n    Front: B\n",
        )
        result = check_graph_health(tmp_path)
        assert result.ok is True
        assert len(result.isolated_nodes) == 2

    def test_roots_count(self, tmp_path: Path):
        self._make_vault(
            tmp_path,
            "  - id: arete_A\n    Front: A\n    deps:\n      requires: [arete_B]\n"
            "  - id: arete_B\n    Front: B\n",
        )
        result = check_graph_health(tmp_path)
        # arete_B is a root: has dependents (A depends on it) but no prerequisites
        assert result.roots == 1

    def test_empty_vault(self, tmp_path: Path):
        result = check_graph_health(tmp_path)
        assert result.ok is True
        assert result.total_nodes == 0


# ---------------------------------------------------------------------------
# filter_graph_by_deck
# ---------------------------------------------------------------------------


class TestFilterGraphByDeck:
    def test_filters_by_file_deck(self, tmp_path: Path):
        md = "---\narete: true\ndeck: Math\ncards:\n  - id: arete_A\n    Front: A\n---\n"
        (tmp_path / "math.md").write_text(md)
        md2 = "---\narete: true\ndeck: History\ncards:\n  - id: arete_B\n    Front: B\n---\n"
        (tmp_path / "history.md").write_text(md2)

        graph = build_graph(tmp_path)
        filtered = filter_graph_by_deck(graph, "Math")
        assert "arete_A" in filtered.nodes
        assert "arete_B" not in filtered.nodes

    def test_filters_by_card_deck_override(self, tmp_path: Path):
        md = (
            "---\narete: true\ndeck: General\ncards:\n"
            "  - id: arete_A\n    Front: A\n    deck: SpecialDeck\n"
            "  - id: arete_B\n    Front: B\n"
            "---\n"
        )
        (tmp_path / "mixed.md").write_text(md)

        graph = build_graph(tmp_path)
        # full deck name: matching is exact-or-subdeck, not substring
        filtered = filter_graph_by_deck(graph, "SpecialDeck")
        assert "arete_A" in filtered.nodes
        assert "arete_B" not in filtered.nodes

    def test_preserves_edges(self, tmp_path: Path):
        md = (
            "---\narete: true\ndeck: Math\ncards:\n"
            "  - id: arete_A\n    Front: A\n    deps:\n      requires: [arete_B]\n"
            "  - id: arete_B\n    Front: B\n"
            "---\n"
        )
        (tmp_path / "math.md").write_text(md)

        graph = build_graph(tmp_path)
        filtered = filter_graph_by_deck(graph, "Math")
        assert filtered.get_prerequisites("arete_A") == ["arete_B"]


def test_unparseable_file_is_reported_not_dropped(tmp_path: Path):
    """An unreadable file must show up in the health result.

    Otherwise its cards vanish from the dependency graph without a trace (row 22).
    """
    from arete.application.queue.graph_resolver import build_graph, check_graph_health

    (tmp_path / "good.md").write_text(
        "---\narete: true\ncards:\n  - id: arete_01GOOD\n    Front: q\n    Back: a\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe not utf-8 at all")

    graph = build_graph(tmp_path)
    assert "arete_01GOOD" in graph.nodes
    assert len(graph.skipped_files) == 1
    assert graph.skipped_files[0][0].endswith("bad.md")

    health = check_graph_health(tmp_path)
    assert health.ok is False
    assert len(health.skipped_files) == 1 and "bad.md" in health.skipped_files[0]


class TestCycleDoesNotScrambleTheQueue:
    """A cycle anywhere in the vault leaves topological_sort deterministic."""

    @staticmethod
    def _graph():
        graph = DependencyGraph()
        for cid in ("P", "Q", "X", "Y", "Z"):
            graph.add_node(CardNode(cid, cid, f"/{cid}.md", 1))
        graph.add_requires("Q", "P")  # P before Q
        graph.add_requires("X", "Y")  # X <-> Y: a cycle unrelated to P/Q
        graph.add_requires("Y", "X")
        graph.add_requires("Z", "Y")  # Z depends on the cycle
        return graph

    @pytest.mark.parametrize(
        "order",
        [["P", "Q", "X", "Y", "Z"], ["Q", "P", "Z", "Y", "X"], ["X", "Z", "Q", "Y", "P"]],
    )
    def test_prereqs_still_come_first(self, order):
        result = topological_sort(self._graph(), order)
        assert sorted(result) == ["P", "Q", "X", "Y", "Z"]
        assert result.index("P") < result.index("Q")
        assert result.index("X") < result.index("Z")
        assert result.index("Y") < result.index("Z")

    def test_same_input_same_output(self):
        order = ["Q", "P", "Z", "Y", "X"]
        assert topological_sort(self._graph(), order) == topological_sort(self._graph(), order)


def _deck_vault(tmp_path):
    def note(name, deck, cards):
        body = "".join(
            f"  - id: {cid}\n    Front: q\n    Back: a\n"
            + (f"    deck: {card_deck}\n" if card_deck else "")
            + (f"    deps:\n      requires: [{req}]\n" if req else "")
            for cid, card_deck, req in cards
        )
        (tmp_path / f"{name}.md").write_text(f"---\narete: true\ndeck: {deck}\ncards:\n{body}---\n")

    note("m", "Math", [("arete_M1", None, "arete_P1"), ("arete_M2", "History", None)])
    note("am", "Applied Mathematics", [("arete_AM1", None, None)])
    note("h", "History", [("arete_H1", "Math::Algebra", None)])
    note("p", "Prob", [("arete_P1", None, None)])
    return tmp_path


class TestDeckFilter:
    """filter_graph_by_deck matches decks the way sync and Anki's `deck:` search do."""

    def test_deck_matches_exactly_or_as_a_subdeck(self, tmp_path):
        """Deck "Math" keeps Math and its subdecks, not "Applied Mathematics"."""
        graph = filter_graph_by_deck(build_graph(_deck_vault(tmp_path)), "Math")
        assert "arete_AM1" not in graph.nodes
        assert "arete_H1" in graph.nodes  # card deck Math::Algebra is a Math subdeck

    def test_card_deck_wins_over_file_deck_like_sync(self, tmp_path):
        """A card's own deck decides, not its file's."""
        graph = filter_graph_by_deck(build_graph(_deck_vault(tmp_path)), "Math")
        assert "arete_M2" not in graph.nodes  # file says Math, card says History
        assert "arete_M1" in graph.nodes

    def test_cross_deck_prereq_is_not_called_isolated(self, tmp_path):
        """M1 requires P1 (deck Prob); filtered to Math, M1 is not isolated."""
        health = check_graph_health(_deck_vault(tmp_path), deck_filter="Math")
        assert "arete_M1" not in [e.card_id for e in health.isolated_nodes]

    def test_filter_reuses_the_decks_the_build_read(self, tmp_path, monkeypatch):
        """The build already parsed every Arete file; the filter must not parse them again."""
        from arete.application.queue import graph_resolver

        graph = build_graph(_deck_vault(tmp_path))
        parses: list[str] = []
        real = graph_resolver.parse_frontmatter
        monkeypatch.setattr(
            graph_resolver, "parse_frontmatter", lambda t: parses.append(t) or real(t)
        )

        filtered = filter_graph_by_deck(graph, "Math")

        assert parses == []
        assert set(filtered.nodes) == {"arete_M1", "arete_H1"}

    def test_a_hand_built_graph_still_reads_decks_from_its_files(self, tmp_path):
        """A graph without recorded decks falls back to the files its nodes name."""
        built = build_graph(_deck_vault(tmp_path))
        graph = DependencyGraph()
        for node in built.nodes.values():
            graph.add_node(node)

        assert set(filter_graph_by_deck(graph, "Math").nodes) == {"arete_M1", "arete_H1"}

    def test_a_duplicate_id_matches_on_either_cards_deck(self, tmp_path):
        """The first card keeps the id; a duplicate in another file still lends its deck."""
        (tmp_path / "a.md").write_text(
            "---\narete: true\ndeck: Math\ncards:\n"
            "  - id: arete_X\n    Front: q\n  - id: arete_A\n    Front: q\n---\n"
        )
        (tmp_path / "b.md").write_text(
            "---\narete: true\ndeck: Bio\ncards:\n"
            "  - id: arete_X\n    Front: q\n  - id: arete_B\n    Front: q\n---\n"
        )
        graph = build_graph(tmp_path)

        assert set(filter_graph_by_deck(graph, "Bio").nodes) == {"arete_X", "arete_B"}
        assert set(filter_graph_by_deck(graph, "Math").nodes) == {"arete_X", "arete_A"}


class TestSilentGraphProblemsAreReported:
    """A reference or id the graph cannot use is reported, never dropped."""

    @staticmethod
    def _note(root, rel, cards):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"  - id: {cid}\n    Front: q\n{deps}" for cid, deps in cards)
        path.write_text(f"---\narete: true\ndeck: D\ncards:\n{body}---\n")

    def test_same_basename_in_two_folders_is_reported_not_merged(self, tmp_path):
        """`Intro` in both math/ and bio/ is ambiguous: reported, resolved to neither."""
        self._note(tmp_path, "math/Intro.md", [("arete_M1", "")])
        self._note(tmp_path, "bio/Intro.md", [("arete_B1", "")])
        self._note(tmp_path, "Calc.md", [("arete_C", "    deps:\n      requires: [Intro]\n")])
        graph = build_graph(tmp_path)
        assert graph.get_prerequisites("arete_C") == []
        assert any("ambiguous" in r for r in graph.unresolved_refs["arete_C"])

    def test_a_path_qualified_ref_picks_one_file(self, tmp_path):
        """`math/Intro` picks one of several namesakes."""
        self._note(tmp_path, "math/Intro.md", [("arete_M1", "")])
        self._note(tmp_path, "bio/Intro.md", [("arete_B1", "")])
        self._note(tmp_path, "Calc.md", [("arete_C", "    deps:\n      requires: [math/Intro]\n")])
        assert build_graph(tmp_path).get_prerequisites("arete_C") == ["arete_M1"]

    def test_a_cross_folder_namesake_is_not_skipped_as_self(self, tmp_path):
        """bio/Intro requiring math/Intro is an edge, not a same-file reference."""
        self._note(tmp_path, "math/Intro.md", [("arete_M1", "")])
        self._note(
            tmp_path, "bio/Intro.md", [("arete_B1", "    deps:\n      requires: [math/Intro]\n")]
        )
        assert build_graph(tmp_path).get_prerequisites("arete_B1") == ["arete_M1"]

    def test_scalar_and_non_string_refs_are_resolved_or_reported(self, tmp_path):
        """A scalar `requires: Algebra` and an int `[2024]` resolve like string list items."""
        self._note(tmp_path, "Algebra.md", [("arete_A", "")])
        self._note(tmp_path, "2024.md", [("arete_Y", "")])
        self._note(
            tmp_path,
            "X.md",
            [
                ("arete_X1", "    deps:\n      requires: Algebra\n"),
                ("arete_X2", "    deps:\n      requires: [2024]\n"),
            ],
        )
        graph = build_graph(tmp_path)
        assert graph.get_prerequisites("arete_X1") == ["arete_A"]
        assert graph.get_prerequisites("arete_X2") == ["arete_Y"]

    def test_a_duplicate_card_id_is_reported_and_fails_the_check(self, tmp_path):
        """Two files with one id: both are reported and the health check fails."""
        self._note(tmp_path, "a.md", [("arete_DUP", "")])
        self._note(tmp_path, "b.md", [("arete_DUP", "")])
        health = check_graph_health(tmp_path)
        assert not health.ok
        assert [d.card_id for d in health.duplicate_ids] == ["arete_DUP"]
        assert sorted(pathlib.Path(f).name for f in health.duplicate_ids[0].files) == [
            "a.md",
            "b.md",
        ]

    def test_ids_differing_only_in_case_are_one_id_as_the_sync_holds(self, tmp_path):
        """Anki matches tags ignoring case, so the sync refuses both cards; so does the check."""
        self._note(tmp_path, "a.md", [("arete_01J00000000000000000000DUP", "")])
        self._note(tmp_path, "b.md", [("ARETE_01j00000000000000000000dup", "")])
        health = check_graph_health(tmp_path)
        assert not health.ok
        [dup] = health.duplicate_ids  # named as the first file scanned writes it
        assert dup.card_id.casefold() == "arete_01j00000000000000000000dup"
        assert sorted(pathlib.Path(f).name for f in health.duplicate_ids[0].files) == [
            "a.md",
            "b.md",
        ]


def test_export_graph_is_what_the_plugin_draws(tmp_path: Path):
    """The plugin draws this export; it resolves no reference itself."""
    from arete.application.queue.graph_resolver import export_graph

    def note(rel: str, body: str) -> None:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\narete: true\ncards:\n{body}---\n", encoding="utf-8")

    note("math/Intro.md", "  - id: arete_M\n    Front: math intro\n    Back: a\n")
    note("bio/Intro.md", "  - id: arete_B\n    Front: bio intro\n    Back: a\n")
    note(
        "Algebra.md",
        "  - id: arete_A\n    Front: algebra\n    Back: a\n"
        "    deps:\n      requires: math/Intro\n      related: [Intro]\n",
    )

    out = export_graph(tmp_path)
    nodes = {n.id: n for n in out.nodes}
    assert nodes["arete_A"].file == "Algebra.md"
    assert nodes["arete_A"].title == "algebra"  # root-level Front, not the id
    assert out.requires == [["arete_A", "arete_M"]]  # scalar folder/name ref resolves
    assert out.related == []  # bare `Intro` names two files: reported, not guessed
    assert any("ambiguous" in r for r in out.unresolved_refs["arete_A"])


class TestBuildGraphSkipsNotesWithoutCards:
    """A note whose frontmatter cannot set the `arete` key is not YAML-parsed."""

    def test_every_spelling_of_the_arete_key_is_still_read(self, tmp_path: Path):
        card = "cards:\n  - id: arete_{0}\n    Front: q\n    Back: a\n---\n$\\alpha$\n"
        (tmp_path / "plain.md").write_text("---\narete: true\n" + card.format("P"))
        (tmp_path / "dq.md").write_text('---\n"arete": true\n' + card.format("D"))
        (tmp_path / "sq.md").write_text("---\n'arete': true\n" + card.format("S"))
        (tmp_path / "sp.md").write_text("---\narete : true\n" + card.format("W"))
        (tmp_path / "esc.md").write_text('---\n"\\x61rete": true\n' + card.format("E"))
        (tmp_path / "bom.md").write_text("\ufeff---\r\narete: true\n" + card.format("B"))

        graph = build_graph(tmp_path)

        assert set(graph.nodes) == {
            "arete_P",
            "arete_D",
            "arete_S",
            "arete_W",
            "arete_E",
            "arete_B",
        }

    def test_math_in_the_body_does_not_force_a_parse(self, tmp_path: Path, monkeypatch):
        from arete.application.queue import graph_resolver

        (tmp_path / "math.md").write_text("---\ntags: [x]\n---\n$\\alpha$ and arete\n")
        (tmp_path / "none.md").write_text("# no frontmatter, $\\beta$\n")
        (tmp_path / "fm.md").write_text('---\ntitle: "a\\tb"\n---\nbody\n')
        parsed: list[str] = []
        real = graph_resolver.parse_frontmatter
        monkeypatch.setattr(
            graph_resolver, "parse_frontmatter", lambda t: parsed.append(t) or real(t)
        )

        build_graph(tmp_path)

        # Only the note with a backslash in its frontmatter is parsed.
        assert [t.splitlines()[1] for t in parsed] == ['title: "a\\tb"']

    def test_an_undecodable_file_is_still_reported(self, tmp_path: Path):
        (tmp_path / "bad.md").write_bytes(b"\xff\xfe not utf-8")
        assert [p for p, _ in build_graph(tmp_path).skipped_files] == [str(tmp_path / "bad.md")]


_FM_LINES = st.sampled_from(
    [
        "---",
        "--- ",
        "---\r",
        " ---",
        "----",
        "  ---",
        "",
        "arete: true",
        '"arete": true',
        "'arete': true",
        "arete : true",
        '"\\x61rete": true',
        "cards: []",
        "tags: [a]",
        "x: |",
        "  indented",
        "- item",
        "just text",
        "$\\alpha$",
        "\tkey: v",
        "\x0c",
    ]
)


@given(lines=st.lists(_FM_LINES, max_size=8), bom=st.booleans())
@settings(max_examples=500, deadline=None)
def test_the_pre_check_never_skips_a_note_the_parser_would_accept(lines, bom):
    """The slice has the parser's bounds, and a skipped note never parses to `arete: true`."""
    from arete.application.queue.graph_resolver import _frontmatter_slice, _may_hold_cards
    from arete.application.utils.text import parse_frontmatter

    text = ("\ufeff" if bom else "") + "\n".join(lines)
    meta, body = parse_frontmatter(text)
    fm = _frontmatter_slice(text)
    split = text.lstrip("﻿").split("\n")
    if split[0].strip() != "---":
        assert fm == "" and meta == {}
    elif fm is None:
        assert meta == {} and body == text.lstrip("﻿")  # the parser found no closing line
    elif isinstance(meta, dict) and "__yaml_error__" in meta:
        assert body == text.lstrip("﻿")  # a YAML error hands back the whole text
    else:
        # The parser's body starts right after the closing line the slice stops at.
        closing = 1 if split[1].rstrip() == "---" else 2 + fm.count("\n")
        assert body == "\n".join(split[closing + 1 :])
    if not _may_hold_cards(text):
        assert not (isinstance(meta, dict) and meta.get("arete") is True)


# --- Paths in output: vault-relative, with "/", on every OS ---


@pytest.mark.parametrize(
    ("root", "path"),
    [
        (PurePosixPath("/Users/jo/Obsidian Vault"), "/Users/jo/Obsidian Vault/sub dir/Cramér.md"),
        (
            PureWindowsPath(r"C:\Users\Jo Doe\Obsidian Vault"),
            r"C:\Users\Jo Doe\Obsidian Vault\sub dir\Cramér.md",
        ),
    ],
    ids=["posix", "windows"],
)
def test_a_note_is_named_by_the_same_relative_path_on_every_os(root, path):
    assert vault_relative(path, root) == "sub dir/Cramér.md"


def test_a_path_outside_the_vault_is_named_as_it_is():
    assert vault_relative("/elsewhere/a.md", PurePosixPath("/vault")) == "/elsewhere/a.md"


def test_graph_check_names_files_relative_to_the_vault(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    card = (
        "cards:\n  - id: arete_{0}\n    Front: q\n    Back: a\n    deps:\n      requires: [{1}]\n"
    )
    (tmp_path / "sub" / "a.md").write_text(
        "---\narete: true\ndeck: D\n" + card.format("A", "missing") + "---\n", encoding="utf-8"
    )
    (tmp_path / "sub" / "b.md").write_text(
        "---\narete: true\ndeck: D\n" + card.format("A", "x") + "---\n", encoding="utf-8"
    )
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe not utf-8 at all")

    health = check_graph_health(tmp_path)

    assert [e.file for e in health.unresolved_refs] in (["sub/a.md"], ["sub/b.md"])
    assert sorted(health.duplicate_ids[0].files) == ["sub/a.md", "sub/b.md"]
    assert [s.split(":")[0] for s in health.skipped_files] == ["bad.md"]
