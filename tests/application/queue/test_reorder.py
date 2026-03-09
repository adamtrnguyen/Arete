"""Tests for queue session file writer."""

import json

from arete.application.queue.reorder import (
    clear_queue_session,
    write_queue_session,
)
from arete.domain.graph import CardNode, DependencyGraph


def _make_graph() -> DependencyGraph:
    """Build a test graph.

    Prereq1 → CardA → CardC
    Prereq2 → CardB → CardC
    CardD (isolated)

    Where → means "is required by".
    """
    g = DependencyGraph()
    for nid in ["prereq1", "prereq2", "cardA", "cardB", "cardC", "cardD"]:
        g.add_node(CardNode(id=nid, title=nid, file_path=f"{nid}.md", line_number=1))

    g.add_requires("cardA", "prereq1")
    g.add_requires("cardB", "prereq2")
    g.add_requires("cardC", "cardA")
    g.add_requires("cardC", "cardB")
    return g


class TestWriteQueueSession:
    def test_writes_session_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            "arete.application.queue.reorder.SESSION_FILE", tmp_path / "queue_session.json"
        )

        graph = _make_graph()
        ordered = ["prereq1", "cardA", "cardC"]
        id_to_cid = {"prereq1": 100, "cardA": 200, "cardC": 300}

        path = write_queue_session(graph, ordered, id_to_cid, "Test::Queue")

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["deck_name"] == "Test::Queue"
        assert data["queue_order"] == ordered

    def test_includes_prereqs_from_graph(self, tmp_path, monkeypatch):
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            "arete.application.queue.reorder.SESSION_FILE", tmp_path / "queue_session.json"
        )

        graph = _make_graph()
        ordered = ["prereq1", "cardA", "cardC"]
        id_to_cid = {"prereq1": 100, "cardA": 200, "cardC": 300}

        path = write_queue_session(graph, ordered, id_to_cid, "Test::Queue")
        data = json.loads(path.read_text())

        # cardA requires prereq1
        assert "prereq1" in data["cards"]["cardA"]["prereqs"]
        # cardC requires cardA and cardB
        assert "cardA" in data["cards"]["cardC"]["prereqs"]

    def test_includes_cid_mapping(self, tmp_path, monkeypatch):
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            "arete.application.queue.reorder.SESSION_FILE", tmp_path / "queue_session.json"
        )

        graph = _make_graph()
        ordered = ["prereq1", "cardA"]
        id_to_cid = {"prereq1": 100, "cardA": 200}

        path = write_queue_session(graph, ordered, id_to_cid, "Test::Queue")
        data = json.loads(path.read_text())

        assert data["cards"]["prereq1"]["cid"] == 100
        assert data["cards"]["cardA"]["cid"] == 200

    def test_skips_cards_without_cid(self, tmp_path, monkeypatch):
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            "arete.application.queue.reorder.SESSION_FILE", tmp_path / "queue_session.json"
        )

        graph = _make_graph()
        ordered = ["prereq1", "cardA", "cardD"]
        id_to_cid = {"prereq1": 100, "cardA": 200}  # cardD has no CID

        path = write_queue_session(graph, ordered, id_to_cid, "Test::Queue")
        data = json.loads(path.read_text())

        assert "cardD" not in data["cards"]
        assert "prereq1" in data["cards"]

    def test_isolated_card_has_no_prereqs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            "arete.application.queue.reorder.SESSION_FILE", tmp_path / "queue_session.json"
        )

        graph = _make_graph()
        ordered = ["cardD"]
        id_to_cid = {"cardD": 400}

        path = write_queue_session(graph, ordered, id_to_cid, "Test::Queue")
        data = json.loads(path.read_text())

        assert data["cards"]["cardD"]["prereqs"] == []


class TestClearQueueSession:
    def test_clears_existing_file(self, tmp_path, monkeypatch):
        session_file = tmp_path / "queue_session.json"
        session_file.write_text("{}")
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_FILE", session_file)

        clear_queue_session()
        assert not session_file.exists()

    def test_noop_if_missing(self, tmp_path, monkeypatch):
        session_file = tmp_path / "queue_session.json"
        monkeypatch.setattr("arete.application.queue.reorder.SESSION_FILE", session_file)

        # Should not raise
        clear_queue_session()
