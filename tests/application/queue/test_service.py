"""Tests for the queue orchestration service."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from arete.application.queue import graph_resolver
from arete.application.queue.service import build_study_queue


class _FakeAnki:
    """A QueuePort that never touches Anki."""

    def __init__(self, arete_ids: list[str]):
        self.arete_ids = arete_ids

    async def get_due_cards(self, deck, include_new=False):
        return list(range(len(self.arete_ids)))

    async def map_nids_to_arete_ids(self, nids):
        return list(self.arete_ids)

    async def get_card_ids_for_arete_ids(self, arete_ids):
        return list(range(1, len(arete_ids) + 1))

    async def create_topo_deck(self, name, cids, reschedule=True):
        return True


def _write_vault(root: Path) -> None:
    (root / "Algebra.md").write_text(
        "---\narete: true\ndeck: Math\ncards:\n"
        "  - id: arete_A\n    Front: a\n    Back: a\n"
        "  - id: arete_B\n    Front: b\n    Back: b\n"
        "    deps:\n      requires: [arete_A]\n"
        "---\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("algo", ["static", "dynamic"])
@pytest.mark.parametrize("enrich", [True, False])
def test_one_request_builds_the_graph_once(tmp_path: Path, algo, enrich):
    """Each graph build reads and parses the whole vault, so a request pays for one."""
    _write_vault(tmp_path)
    real = graph_resolver.build_graph
    calls: list[Path] = []

    def counting(vault_root):
        calls.append(vault_root)
        return real(vault_root)

    with (
        patch("arete.application.queue.service.build_graph", counting),
        patch("arete.application.queue.builder.build_graph", counting),
    ):
        result = asyncio.run(
            build_study_queue(_FakeAnki(["arete_B"]), tmp_path, algo=algo, enrich=enrich)
        )

    assert calls == [tmp_path]
    assert [item.id for item in result.queue_items] == ["arete_A", "arete_B"]
    if enrich:
        assert [item.title for item in result.queue_items] == ["a", "b"]


@pytest.mark.parametrize(
    "module", ["arete.application.queue.service", "arete.domain.graph", "arete.interface.cli"]
)
def test_importing_the_queue_path_does_not_load_networkx(module):
    """Every CLI command imports the queue service; networkx loads only for graph work."""
    import subprocess
    import sys

    code = f"import sys, {module}; print('networkx' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "False"
