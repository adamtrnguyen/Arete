"""Prune deletes only what Arete made, and only when someone confirmed it.

The rules are in `application/sync/prune.py`. Each test here pins one of them against an
in-memory Anki that answers the four calls prune makes the way both backends do: a deck
search includes subdecks, and tags come back per note.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import respx
from httpx import Response

from arete.application.config import AppConfig
from arete.application.sync.id_service import generate_arete_id
from arete.application.sync.prune import prune_orphans
from arete.application.utils.logging import RunRecorder
from arete.domain.interfaces import SyncPort
from arete.domain.models import AnkiDeck
from arete.domain.ownership import is_arete_id
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter

LOG = logging.getLogger("test-prune")


def aid(n: int) -> str:
    """A well-formed Arete id: `arete_` + 26 ULID characters."""
    return f"arete_{n:026d}"


class FakeAnki(SyncPort):
    """An in-memory Anki for prune."""

    def __init__(self, notes: dict[int, tuple[str, list[str]]], extra_decks=()):
        """Notes are {nid: (deck, tags)}. `extra_decks` are decks that hold no note."""
        self.notes = dict(notes)
        self.decks = {"Default"} | set(extra_decks)
        for deck, _ in notes.values():
            self.decks.add(deck)
            self.decks.update(AnkiDeck(name=deck).parents)
        self.deleted_notes: list[int] = []
        self.deleted_decks: list[str] = []
        self.fail_delete_notes = False
        self.add_after_delete: tuple[int, str] | None = None  # a note that appears mid-prune

    async def get_deck_names(self):
        return sorted(self.decks)

    async def get_notes_in_deck(self, deck):
        return {
            str(n): n for n, (d, _) in self.notes.items() if d == deck or d.startswith(deck + "::")
        }

    async def get_note_tags(self, nids):
        return {n: list(self.notes[n][1]) for n in nids if n in self.notes}

    async def delete_notes(self, nids):
        if self.fail_delete_notes:
            raise RuntimeError("Anki said no")
        for n in nids:
            self.notes.pop(n)
            self.deleted_notes.append(n)
        if self.add_after_delete:
            nid, deck = self.add_after_delete
            self.notes[nid] = (deck, [])

    async def delete_decks(self, names):
        for name in names:
            for d in [d for d in self.decks if d == name or d.startswith(name + "::")]:
                self.decks.discard(d)
            for n in [n for n, (d, _) in self.notes.items() if d == name]:
                self.notes.pop(n)
            self.deleted_decks.append(name)


def config(**overrides) -> AppConfig:
    fields = {
        "root_input": Path("/v"),
        "vault_root": Path("/v"),
        "dry_run": False,
        "force": False,
        "prune": True,
    }
    fields.update(overrides)
    return AppConfig.model_construct(**fields)


def recorder(nids=(), decks=()) -> RunRecorder:
    r = RunRecorder()
    r.add_inventory([{"nid": n, "deck": None} for n in nids])
    r.add_inventory([{"nid": None, "deck": d} for d in decks])
    return r


async def prune(anki, rec=None, cfg=None, **kwargs):
    return await prune_orphans(
        cfg or config(),
        rec or recorder(),
        anki,
        LOG,
        **{"confirmed": True, "declared_ids": (), **kwargs},
    )


# ---------------------------------------------------------------------------
# The ownership marker
# ---------------------------------------------------------------------------


def test_every_generated_id_is_an_ownership_marker():
    """The writer (id_service) and the prune reader must agree on the format."""
    assert all(is_arete_id(generate_arete_id()) for _ in range(50))


@pytest.mark.parametrize(
    "tag",
    ["arete_review", "arete_", "arete_A1", "Arete", "japanese", aid(1) + "X", "x" + aid(1)],
)
def test_a_tag_that_only_looks_like_an_arete_id_is_not_one(tag):
    assert not is_arete_id(tag)


def test_a_generated_looking_id_is_one_in_either_case():
    assert is_arete_id(aid(7))
    assert is_arete_id(aid(7).lower())


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hand_made_deck_with_hand_made_notes_survives():
    anki = FakeAnki({1: ("Personal::Japanese", ["vocab"]), 2: ("Work", [])})
    report = await prune(anki)
    assert anki.deleted_notes == [] and anki.deleted_decks == []
    assert report.status == "clean"


@pytest.mark.asyncio
async def test_an_arete_orphan_note_is_deleted_and_its_vault_deck_kept():
    anki = FakeAnki({1: ("Math", [aid(1)]), 2: ("Math", [aid(2)])})
    report = await prune(anki, recorder(nids=["1"], decks=["Math"]))
    assert anki.deleted_notes == [2]
    assert anki.deleted_decks == []
    assert report.status == "deleted"
    assert [(n.nid, n.arete_id, n.deck) for n in report.notes] == [(2, aid(2), "Math")]


@pytest.mark.asyncio
async def test_a_mixed_deck_keeps_its_hand_made_notes_and_is_not_deleted():
    anki = FakeAnki({1: ("Old", [aid(1)]), 2: ("Old", ["mine"]), 3: ("Old::Sub", [aid(3)])})
    report = await prune(anki)
    assert sorted(anki.deleted_notes) == [1, 3]
    assert 2 in anki.notes
    assert anki.deleted_decks == ["Old::Sub"]  # holds only an Arete orphan
    assert "Old" in report.decks_kept


@pytest.mark.asyncio
async def test_a_look_alike_tag_does_not_make_a_note_deletable():
    anki = FakeAnki({1: ("Lists", ["arete_review"])})
    await prune(anki)
    assert anki.deleted_notes == [] and anki.deleted_decks == []


@pytest.mark.asyncio
async def test_a_note_with_a_card_under_default_is_never_deleted():
    anki = FakeAnki({1: ("Default", [aid(1)])})
    await prune(anki)
    assert anki.deleted_notes == []


@pytest.mark.asyncio
async def test_a_note_whose_tags_cannot_be_read_is_kept():
    anki = FakeAnki({1: ("Gone", [aid(1)])})

    async def no_tags(nids):
        return {}

    anki.get_note_tags = no_tags
    report = await prune(anki)
    assert anki.deleted_notes == [] and anki.deleted_decks == []
    assert report.decks_kept == ["Gone"]


@pytest.mark.asyncio
async def test_a_note_whose_arete_id_the_vault_declares_is_kept():
    """Its nid is not in the vault (the write-back never landed), but its id is."""
    anki = FakeAnki({1: ("Stats", [aid(1)]), 2: ("Stats", [aid(2)])})

    report = await prune(anki, declared_ids={aid(1).upper().replace("ARETE", "arete")})

    assert anki.deleted_notes == [2]
    assert [n.nid for n in report.notes] == [2]
    assert anki.deleted_decks == [] and report.decks_kept == ["Stats"]


@pytest.mark.asyncio
async def test_a_run_with_errors_deletes_nothing_and_lists_what_it_would():
    anki = FakeAnki({1: ("Old", [aid(1)])})
    rec = recorder()
    rec.add_error(Path("/v/Bayes.md"), "Sync fail: model was not found", "#1")

    report = await prune(anki, rec)

    assert anki.deleted_notes == [] and anki.deleted_decks == []
    assert report.status == "withheld"
    assert [n.nid for n in report.notes] == [1] and report.decks == ["Old"]
    assert "Bayes.md" in report.message and "Nothing was deleted" in report.message
    assert (
        report.summary()
        == "withheld, the run had errors; deleted nothing (1 notes and 1 decks listed)"
    )


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_deck_of_arete_orphans_is_deleted_from_the_top_of_its_subtree():
    anki = FakeAnki({1: ("Old", [aid(1)]), 2: ("Old::Sub", [aid(2)])})
    report = await prune(anki)
    assert sorted(anki.deleted_notes) == [1, 2]
    assert anki.deleted_decks == ["Old"]  # Old::Sub goes with it
    assert report.decks == ["Old"]


@pytest.mark.asyncio
async def test_vault_decks_and_their_parents_are_never_deleted():
    anki = FakeAnki(
        {1: ("Math::Algebra::Linear", [aid(1)]), 2: ("OrphanDeck", [aid(2)])},
        extra_decks=["Math", "Math::Algebra"],
    )
    await prune(anki, recorder(nids=["1"], decks=["Math::Algebra::Linear"]))
    assert anki.deleted_decks == ["OrphanDeck"]


@pytest.mark.asyncio
async def test_an_empty_deck_is_listed_and_not_deleted():
    anki = FakeAnki({1: ("Old", [aid(1)])}, extra_decks=["Empty", "Old::Empty"])
    report = await prune(anki)
    assert anki.deleted_notes == [1]
    assert "Empty" in anki.decks and "Old::Empty" in anki.decks
    assert report.empty_decks_not_deleted == ["Empty", "Old::Empty"]
    # Deleting Old would take its empty subdeck with it, so Old stays too.
    assert anki.deleted_decks == [] and "Old" in report.decks_kept


@pytest.mark.asyncio
async def test_a_deck_holding_a_note_the_vault_claims_is_kept():
    anki = FakeAnki({1: ("Moved", [aid(1)])})
    report = await prune(anki, recorder(nids=["1"], decks=["Elsewhere"]))
    assert anki.deleted_notes == [] and anki.deleted_decks == []
    assert report.status == "clean"


@pytest.mark.asyncio
async def test_a_deck_that_gained_a_note_during_the_prune_is_kept():
    anki = FakeAnki({1: ("Old", [aid(1)])})
    anki.add_after_delete = (99, "Old")
    report = await prune(anki)
    assert anki.deleted_notes == [1]
    assert anki.deleted_decks == [] and 99 in anki.notes
    assert report.decks == []


@pytest.mark.asyncio
async def test_a_failed_note_delete_deletes_no_deck():
    anki = FakeAnki({1: ("Old", [aid(1)])})
    anki.fail_delete_notes = True
    report = await prune(anki)
    assert report.status == "failed"
    assert anki.deleted_decks == []


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def _no_input(*_args):
    raise AssertionError("prune called input()")


@pytest.mark.asyncio
async def test_unconfirmed_off_a_terminal_returns_the_preview_and_deletes_nothing(monkeypatch):
    monkeypatch.setattr("builtins.input", _no_input)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    prompt = MagicMock(return_value=True)
    anki = FakeAnki({1: ("Old", [aid(1)]), 2: ("Math", [aid(2)])})

    report = await prune(anki, recorder(decks=["Math"]), confirmed=False, prompt=prompt)

    prompt.assert_not_called()
    assert anki.deleted_notes == [] and anki.deleted_decks == []
    assert report.status == "unconfirmed"
    assert report.decks == ["Old"]
    assert sorted(n.nid for n in report.notes) == [1, 2]
    assert report.to_dict()["note_count"] == 2 and report.to_dict()["deck_count"] == 1


@pytest.mark.asyncio
async def test_force_does_not_confirm_a_prune(monkeypatch):
    monkeypatch.setattr("builtins.input", _no_input)
    anki = FakeAnki({1: ("Old", [aid(1)])})
    report = await prune(anki, cfg=config(force=True), confirmed=False)
    assert report.status == "unconfirmed" and anki.deleted_notes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "status", "deleted"), [(True, "deleted", [1]), (False, "aborted", [])]
)
async def test_at_a_terminal_the_prompt_decides(monkeypatch, answer, status, deleted):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompt = MagicMock(return_value=answer)
    anki = FakeAnki({1: ("Old", [aid(1)])})
    report = await prune(anki, confirmed=False, prompt=prompt)
    prompt.assert_called_once()
    assert report.status == status and anki.deleted_notes == deleted


@pytest.mark.asyncio
async def test_a_dry_run_deletes_nothing_even_when_confirmed():
    anki = FakeAnki({1: ("Old", [aid(1)])})
    report = await prune(anki, cfg=config(dry_run=True))
    assert report.status == "dry_run" and anki.deleted_notes == []


@pytest.mark.asyncio
async def test_prune_is_skipped_unless_the_run_covers_the_whole_vault():
    anki = FakeAnki({1: ("Old", [aid(1)])})
    report = await prune(anki, cfg=config(root_input=Path("/v/sub")))
    assert report.status == "skipped" and anki.deleted_notes == []


# ---------------------------------------------------------------------------
# Through the AnkiConnect adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_over_ankiconnect_only_the_arete_orphan_deck_is_deleted():
    adapter = AnkiConnectAdapter(url="http://mock:8765")
    decks = {"Math": [1], "Math::Algebra": [1], "OrphanDeck": [2], "Personal": [3]}
    tags = {1: [aid(1)], 2: [aid(2)], 3: ["mine"]}

    def answer(request):
        data = json.loads(request.content)
        action, params = data["action"], data.get("params", {})
        if action == "deckNames":
            return Response(200, json={"result": ["Default", *decks], "error": None})
        if action == "findNotes":
            deck = params["query"].strip('"').removeprefix("deck:")
            alive = [n for n in decks.get(deck, []) if n not in deleted]
            return Response(200, json={"result": alive, "error": None})
        if action == "notesInfo":
            info = [{"noteId": n, "tags": tags[n]} for n in params["notes"]]
            return Response(200, json={"result": info, "error": None})
        if action == "deleteNotes":
            deleted.update(params["notes"])
        return Response(200, json={"result": None, "error": None})

    deleted: set[int] = set()
    route = respx.post("http://mock:8765").mock(side_effect=answer)
    await prune(adapter, recorder(nids=["1"], decks=["Math::Algebra"]))

    calls = [json.loads(c.request.content) for c in route.calls]
    deck_deletes = [c["params"]["decks"] for c in calls if c["action"] == "deleteDecks"]
    note_deletes = [c["params"]["notes"] for c in calls if c["action"] == "deleteNotes"]
    assert deck_deletes == [["OrphanDeck"]]
    assert note_deletes == [[2]]
