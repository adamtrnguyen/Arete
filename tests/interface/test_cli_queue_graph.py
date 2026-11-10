"""`arete queue` and `arete graph check` against vaults in tmp_path.

The Anki side is a fake bridge handed to the CLI through the factory; the vault, the graph
and the queue algorithms are the real ones.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()


def _note(path: Path, deck: str, cards: list[tuple[str, list[str]]]) -> None:
    """Write a note whose cards are (id, requires) pairs."""
    lines = ["---", "arete: true", f"deck: {deck}", "cards:"]
    for card_id, requires in cards:
        lines += [
            f"  - id: {card_id}",
            f"    Front: front of {card_id}",
            f"    Back: back of {card_id}",
        ]
        if requires:
            lines += ["    deps:", f"      requires: [{', '.join(requires)}]"]
    lines += ["---", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


class FakeBridge:
    """A queue-capable bridge: due notes map straight to the given Arete ids."""

    def __init__(self, due: int, arete_ids: list[str], deck_ok: bool = True):
        """`due` notes are due; `arete_ids` are the ones that carry an Arete id tag."""
        self.due = due
        self.arete_ids = arete_ids
        self.deck_ok = deck_ok
        self.due_args: list[tuple] = []
        self.created: list[tuple[str, list[int], bool]] = []
        self.resolved: list[list[str]] = []
        self.closed = False

    async def get_due_cards(self, deck_name=None, include_new=False):
        self.due_args.append((deck_name, include_new))
        return list(range(1, self.due + 1))

    async def map_nids_to_arete_ids(self, nids):
        return list(self.arete_ids)

    async def get_card_ids_for_arete_ids(self, arete_ids):
        self.resolved.append(list(arete_ids))
        return [100 + i for i, _ in enumerate(arete_ids)]

    async def create_topo_deck(self, deck_name, cids, reschedule=True):
        self.created.append((deck_name, list(cids), reschedule))
        return self.deck_ok

    async def close(self):
        self.closed = True


def _use(monkeypatch, bridge: FakeBridge) -> FakeBridge:
    async def get_bridge(_config):
        return bridge

    monkeypatch.setattr("arete.composition.factory.get_anki_bridge", get_bridge)
    return bridge


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    _note(v / "Algebra.md", "Math", [("arete_A", []), ("arete_B", ["arete_A"])])
    return v


# --- queue ---


def test_queue_with_nothing_due_says_so_and_closes_the_bridge(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(0, []))
    result = runner.invoke(app, ["queue", str(vault)])
    assert result.exit_code == 0
    assert "No cards found." in result.stdout
    assert bridge.closed
    assert bridge.created == []


def test_queue_builds_the_deck_with_prerequisites_first(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(1, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault)])
    assert result.exit_code == 0
    out = result.stdout
    assert "Due cards: 1 (0 without Arete IDs)" in out
    assert "Algorithm: static" in out
    assert "Weak prerequisites: 1" in out
    assert "Main queue: 1" in out
    assert "Created 'Arete::Queue' with 2 cards." in out
    assert bridge.resolved == [["arete_A", "arete_B"]]
    assert bridge.created == [("Arete::Queue", [100, 101], True)]
    assert bridge.closed


def test_queue_dry_run_creates_nothing(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(1, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "Main queue: 1" in result.stdout
    assert "Created" not in result.stdout
    assert bridge.created == []


def test_queue_reports_a_failed_deck_creation(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(1, ["arete_B"], deck_ok=False))
    result = runner.invoke(app, ["queue", str(vault)])
    assert result.exit_code == 0
    assert "Failed to create filtered deck." in result.stdout
    assert "Created" not in result.stdout
    assert len(bridge.created) == 1


def test_queue_warns_about_cards_without_an_arete_id(monkeypatch, vault):
    _use(monkeypatch, FakeBridge(3, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--dry-run"])
    assert "WARNING: 2/3 cards have no Arete ID tag." in result.stdout
    assert "Due cards: 3 (2 without Arete IDs)" in result.stdout


def test_queue_only_unmapped_cards_stops_after_the_warning(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(2, []))
    result = runner.invoke(app, ["queue", str(vault)])
    assert result.exit_code == 0
    assert bridge.created == []
    assert "Created" not in result.stdout


def test_queue_names_missing_prerequisites(monkeypatch, tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "N.md", "Math", [("arete_A", ["arete_gone"])])
    _use(monkeypatch, FakeBridge(1, ["arete_A"]))
    result = runner.invoke(app, ["queue", str(v), "--dry-run"])
    assert "Missing prereqs: ['arete_gone']" in result.stdout


def test_queue_deck_filter_is_isolated_unless_cross_deck(monkeypatch, vault):
    isolated = _use(monkeypatch, FakeBridge(1, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--deck", "Math"])
    assert isolated.due_args == [("Math", False)]
    assert "Weak prerequisites" not in result.stdout
    assert "Created 'Arete::Queue' with 1 cards." in result.stdout

    crossing = _use(monkeypatch, FakeBridge(1, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--deck", "Math", "--cross-deck"])
    assert "Weak prerequisites: 1" in result.stdout
    assert "Created 'Arete::Queue' with 2 cards." in result.stdout
    assert crossing.created[0][1] == [100, 101]


def test_queue_include_new_reaches_the_bridge(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(0, []))
    runner.invoke(app, ["queue", str(vault), "--include-new"])
    assert bridge.due_args == [(None, True)]


def test_queue_reports_cycles(monkeypatch, tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "N.md", "Math", [("arete_A", ["arete_B"]), ("arete_B", ["arete_A"])])
    _use(monkeypatch, FakeBridge(2, ["arete_A", "arete_B"]))
    result = runner.invoke(app, ["queue", str(v), "--dry-run"])
    assert "Cycles detected: 1" in result.stdout


def test_queue_reports_prerequisites_cut_by_the_size_limit(monkeypatch, tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    prereqs = [(f"arete_P{i:02d}", []) for i in range(60)]
    _note(v / "N.md", "Math", [*prereqs, ("arete_M", [p for p, _ in prereqs])])
    _use(monkeypatch, FakeBridge(1, ["arete_M"]))
    result = runner.invoke(app, ["queue", str(v), "--dry-run", "--depth", "1"])
    assert "Left out by the queue size limit:" in result.stdout


def test_queue_json_dry_run_prints_only_the_plan(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(1, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--dry-run", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "deck": "All Decks",
        "due_count": 1,
        "total_with_prereqs": 2,
        "queue": [
            {
                "position": 1,
                "id": "arete_A",
                "title": "front of arete_A",
                "file": str(vault / "Algebra.md"),
                "is_prereq": True,
            },
            {
                "position": 2,
                "id": "arete_B",
                "title": "front of arete_B",
                "file": str(vault / "Algebra.md"),
                "is_prereq": False,
            },
        ],
    }
    assert bridge.created == []
    assert bridge.closed


def test_queue_json_counts_only_cards_with_an_arete_id_and_names_the_deck(monkeypatch, vault):
    _use(monkeypatch, FakeBridge(3, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--dry-run", "--json", "--deck", "Math"])
    out = json.loads(result.stdout)
    assert (out["deck"], out["due_count"], out["total_with_prereqs"]) == ("Math", 1, 1)


def test_queue_json_with_nothing_due_is_an_empty_queue(monkeypatch, vault):
    _use(monkeypatch, FakeBridge(0, []))
    result = runner.invoke(app, ["queue", str(vault), "--dry-run", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "deck": "All Decks",
        "due_count": 0,
        "total_with_prereqs": 0,
        "queue": [],
    }


def test_queue_max_cards_reaches_the_builder(monkeypatch, tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    prereqs = [(f"arete_P{i:02d}", []) for i in range(5)]
    _note(v / "N.md", "Math", [*prereqs, ("arete_M", [p for p, _ in prereqs])])
    _use(monkeypatch, FakeBridge(1, ["arete_M"]))
    result = runner.invoke(app, ["queue", str(v), "--dry-run", "--json", "--max-cards", "3"])
    assert json.loads(result.stdout)["total_with_prereqs"] == 3


def test_queue_json_without_dry_run_builds_the_deck(monkeypatch, vault):
    bridge = _use(monkeypatch, FakeBridge(1, ["arete_B"]))
    result = runner.invoke(app, ["queue", str(vault), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["total_with_prereqs"] == 2
    assert bridge.created == [("Arete::Queue", [100, 101], True)]


def test_queue_json_failed_deck_creation_exits_1_with_the_reason_on_stderr(monkeypatch, vault):
    _use(monkeypatch, FakeBridge(1, ["arete_B"], deck_ok=False))
    result = runner.invoke(app, ["queue", str(vault), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["total_with_prereqs"] == 2
    assert "Failed to create filtered deck." in result.stderr


def test_queue_backend_overrides_reach_the_config(monkeypatch, vault):
    seen = {}

    async def get_bridge(config):
        seen["config"] = config
        return FakeBridge(0, [])

    monkeypatch.setattr("arete.composition.factory.get_anki_bridge", get_bridge)
    args = ["--backend", "ankiconnect", "--anki-connect-url", "http://fake:1"]
    runner.invoke(app, ["queue", str(vault), "--dry-run", "--json", *args])
    assert seen["config"].backend == "ankiconnect"
    assert seen["config"].anki_connect_url == "http://fake:1"


# --- graph check ---


def test_graph_check_clean_vault_exits_0(vault):
    result = runner.invoke(app, ["graph", "check", str(vault)])
    assert result.exit_code == 0
    assert "Nodes: 2  Edges: 1  Components: 1  Roots: 1" in result.stdout
    assert "Cycles: 0" in result.stdout
    assert "Isolated: 0" in result.stdout
    assert "Unresolved: 0" in result.stdout


def test_graph_check_cycle_is_listed_and_fails(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "N.md", "Math", [("arete_A", ["arete_B"]), ("arete_B", ["arete_A"])])
    result = runner.invoke(app, ["graph", "check", str(v)])
    assert result.exit_code == 1
    assert "Cycles: 1" in result.stdout
    assert " -> " in result.stdout


def test_graph_check_unresolved_reference_is_named_and_fails(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "N.md", "Math", [("arete_A", ["arete_gone"])])
    result = runner.invoke(app, ["graph", "check", str(v)])
    assert result.exit_code == 1
    assert "Unresolved references: 1" in result.stdout
    assert "-> arete_gone  (missing)" in result.stdout


def test_graph_check_isolated_cards_are_listed_with_their_file(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "Lonely.md", "Math", [("arete_A", [])])
    result = runner.invoke(app, ["graph", "check", str(v)])
    assert "Isolated cards (no deps in or out): 1" in result.stdout
    assert "(Lonely.md)" in result.stdout


def test_graph_check_multiple_components_are_reported(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "One.md", "Math", [("arete_A", []), ("arete_B", ["arete_A"])])
    _note(v / "Two.md", "Math", [("arete_C", []), ("arete_D", ["arete_C"])])
    result = runner.invoke(app, ["graph", "check", str(v)])
    assert "Connected components: 2" in result.stdout


def test_graph_check_duplicate_ids_name_both_files_and_fail(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "One.md", "Math", [("arete_A", []), ("arete_B", ["arete_A"])])
    _note(v / "Two.md", "Math", [("arete_A", [])])
    result = runner.invoke(app, ["graph", "check", str(v)])
    assert result.exit_code == 1
    assert "Arete ids used by more than one card" in result.stdout
    assert "arete_A: " in result.stdout
    assert "One.md" in result.stdout
    assert "Two.md" in result.stdout


def test_graph_check_unparseable_file_is_named_and_fails(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "Good.md", "Math", [("arete_A", []), ("arete_B", ["arete_A"])])
    (v / "Broken.md").write_bytes(b"\xff\xfe not utf-8")
    result = runner.invoke(app, ["graph", "check", str(v)])
    assert result.exit_code == 1
    assert "Files that could not be parsed" in result.stdout
    assert "Broken.md" in result.stdout


def test_graph_check_json_is_machine_readable_and_still_fails_a_bad_vault(tmp_path):
    v = tmp_path / "v"
    v.mkdir()
    _note(v / "N.md", "Math", [("arete_A", ["arete_gone"])])
    result = runner.invoke(app, ["graph", "check", str(v), "--json"])
    data = json.loads(result.stdout)
    assert data["total_nodes"] == 1
    assert data["unresolved_refs"][0]["missing_refs"] == ["arete_gone"]
