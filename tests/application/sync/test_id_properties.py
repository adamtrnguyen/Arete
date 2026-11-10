"""Property-based tests for ID generation, and for the sync giving cards their ids."""

import re
from pathlib import Path
from unittest.mock import MagicMock

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from arete.application.sync.id_service import generate_arete_id
from arete.application.sync.vault_service import VaultService
from arete.application.utils.text import parse_frontmatter

ARETE_ID_RE = re.compile(r"^arete_[0-9A-Z]{26}$")


@given(st.integers(min_value=0, max_value=100))
@settings(max_examples=50)
def test_ids_always_match_format(_n):
    """generate_arete_id() always matches arete_[A-Z0-9]{26}."""
    aid = generate_arete_id()
    assert ARETE_ID_RE.match(aid), f"Bad ID format: {aid}"


def test_ids_unique():
    """100 generated IDs are all distinct."""
    ids = {generate_arete_id() for _ in range(100)}
    assert len(ids) == 100


def _note(tmp_path: Path, cards: str) -> Path:
    path = tmp_path / "n.md"
    path.write_bytes(f"---\narete: true\ndeck: D\ncards:\n{cards}---\nbody\n".encode())
    return path


def _ids(path: Path) -> list[object]:
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return [c.get("id") if isinstance(c, dict) else None for c in meta["cards"]]


@given(n_with=st.integers(0, 3), n_without=st.integers(0, 3))
@settings(max_examples=30)
def test_assign_ids_gives_an_id_to_each_card_without_one(tmp_path_factory, n_with, n_without):
    assume(n_with + n_without > 0)
    path = _note(
        tmp_path_factory.mktemp("ids"),
        "".join(f"- id: {generate_arete_id()}\n  Front: q\n" for _ in range(n_with))
        + "".join("- Front: q\n" for _ in range(n_without)),
    )
    held = _ids(path)[:n_with]
    service = VaultService(path.parent, MagicMock())

    assigned, meta, refused = service.assign_ids(path)

    assert (assigned, refused) == (n_without, {})
    ids = _ids(path)
    assert ids[:n_with] == held, "an existing id is kept"
    assert all(ARETE_ID_RE.match(str(i)) for i in ids)
    assert [c["id"] for c in meta["cards"]] == ids
    assert service.assign_ids(path)[0] == 0, "a second run assigns nothing"


def test_assign_ids_skips_cards_that_are_not_mappings(tmp_path: Path):
    path = _note(tmp_path, "- not a mapping\n- Front: q\n")
    assigned, _meta, refused = VaultService(tmp_path, MagicMock()).assign_ids(path)
    assert (assigned, refused) == (1, {})
    assert _ids(path)[0] is None


def test_assign_ids_on_a_dry_run_writes_nothing_and_returns_the_ids(tmp_path: Path):
    path = _note(tmp_path, "- Front: q\n")
    before = path.read_bytes()
    assigned, meta, _ = VaultService(tmp_path, MagicMock()).assign_ids(path, dry_run=True)
    assert path.read_bytes() == before
    assert assigned == 1 and ARETE_ID_RE.match(meta["cards"][0]["id"])


def test_assign_ids_refuses_a_flow_card_and_writes_nothing(tmp_path: Path):
    path = _note(tmp_path, "- Front: q\n- {Front: r}\n")
    before = path.read_bytes()
    assigned, _meta, refused = VaultService(tmp_path, MagicMock()).assign_ids(path)
    assert path.read_bytes() == before
    assert assigned == 0 and list(refused) == [1]
