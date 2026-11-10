"""The proving tests of the invariants in CLAUDE.md, run on every backend that can be driven here.

Backends (`sync_harness`): `direct` (a real collection in `tmp_path`), `fake` (the AnkiConnect
adapter over `FakeAnkiPlus`) and `addon` (the AnkiConnect adapter over the add-on's own handler
on a real collection). Scheduling exists only on the two real collections, so invariant 4 runs
on `direct` and `addon`.

A test of an invariant that does not hold today is a strict xfail whose reason says what was
observed. It fails the gate the day the behaviour is fixed; then delete the mark and update the
"Holds today" column in CLAUDE.md. Invariant 3 lives in `test_sync_equivalence.py`.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from arete.application.sync.vault_service import VaultService
from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter
from tests.e2e.sync_harness import (
    BACKENDS,
    AddonBackend,
    Backend,
    CollectionBackend,
    FakeBackend,
    edit_vault,
    make_backend,
    run_sync,
    sync_config,
    vault_cards,
)

ID = "arete_01J00000000000000000000042"
HOME = "Inv::Home"
CARD = f"""---
arete: true
deck: {HOME}
model: Basic
tags:
- inv
cards:
- id: {ID}
  Front: What does the vault hold?
  Back: The vault's answer.
---

Body.
"""


@dataclass
class World:
    """A one-card vault and a backend, with the sync that joins them."""

    root: Path
    vault: Path
    backend: Backend

    @property
    def md(self) -> Path:
        return self.vault / "card.md"

    async def sync(self, *, confirm_prune: bool = False, **config: Any):
        settings = sync_config(self.root, self.vault, self.backend, **config)
        return await run_sync(self.backend, settings, confirm_prune=confirm_prune)

    def nid(self) -> int:
        [note] = self.backend.notes()
        return note.nid

    @property
    def real(self) -> CollectionBackend:
        """The backend, where the test needs a real collection (scheduling, filtered decks)."""
        assert isinstance(self.backend, CollectionBackend)
        return self.backend


def edit_card(md: Path, change: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
    """Edit a vault file through its parsed frontmatter: `change(meta, first_card)`.

    After the first sync the file is in Arete's dumped form (a deck name with `::` becomes a
    block scalar), so edits go through the parser, not through the text.
    """

    def _apply(text: str) -> str:
        meta, body = parse_frontmatter(text)
        change(meta, meta["cards"][0])
        return rebuild_markdown_with_frontmatter(meta, body)

    edit_vault(md, _apply)


def _set(**values: Any) -> Callable[[dict[str, Any], dict[str, Any]], None]:
    """An edit that sets keys of the first card (`None` deletes the key)."""

    def change(_meta: dict[str, Any], card: dict[str, Any]) -> None:
        for key, value in values.items():
            if value is None:
                card.pop(key, None)
            else:
                card[key] = value

    return change


def _world(name: str, root: Path, text: str = CARD) -> World:
    vault = root / "vault"
    vault.mkdir()
    (vault / "card.md").write_text(text, encoding="utf-8")
    return World(root, vault, make_backend(name, root))


@pytest.fixture
def world(request, tmp_path: Path):
    w = _world(request.param, tmp_path)
    yield w
    w.backend.close()


def on(*names: str, xfail: dict[str, str] | None = None):
    """Parametrize `world` over backends; a backend named in `xfail` gets a strict xfail."""
    marks = xfail or {}
    params = [
        pytest.param(n, marks=pytest.mark.xfail(strict=True, reason=marks[n])) if n in marks else n
        for n in names
    ]
    return pytest.mark.parametrize("world", params, indirect=True)


ALL = BACKENDS
REAL = ("direct", "addon")  # backends with a real collection, so with scheduling


# ---------------------------------------------------------------------------
# Invariant 1: Obsidian is ground truth. An edit made in Anki is overwritten.
# ---------------------------------------------------------------------------

STAT_CACHE_SKIP = (
    "the vault file is untouched, so the second sync gets a stat-cache hit and the hot cache "
    "skips the card before it is compared with Anki (parser.py, is_fresh=False): observed 0 "
    "cards sent and "
)


@pytest.mark.asyncio
@on(*ALL, xfail=dict.fromkeys(ALL, STAT_CACHE_SKIP + "Back still 'edited in Anki'"))
async def test_inv1_a_field_edited_in_anki_is_overwritten(world: World):
    await world.sync()
    nid = world.nid()
    world.backend.set_field(nid, "Back", "edited in Anki")

    await world.sync()

    assert world.backend.note(nid).fields["Back"] == "<p>The vault's answer.</p>"


@pytest.mark.asyncio
@on(*ALL, xfail=dict.fromkeys(ALL, STAT_CACHE_SKIP + "the tag 'anki_only' is still on the note"))
async def test_inv1_a_tag_added_in_anki_is_removed(world: World):
    await world.sync()
    nid = world.nid()
    world.backend.add_tag(nid, "anki_only")

    await world.sync()

    assert sorted(world.backend.note(nid).tags) == sorted([ID, "inv"])


@pytest.mark.asyncio
@on(*ALL, xfail=dict.fromkeys(ALL, STAT_CACHE_SKIP + "the card is still in Inv::Elsewhere"))
async def test_inv1_a_deck_move_in_anki_is_moved_back(world: World):
    await world.sync()
    nid = world.nid()
    world.backend.move(nid, "Inv::Elsewhere")

    await world.sync()

    assert [c.home_deck for c in world.backend.note(nid).cards] == [HOME]


@pytest.mark.asyncio
@on(*ALL, xfail=dict.fromkeys(ALL, STAT_CACHE_SKIP + "the note type is still Basic with Extra"))
async def test_inv1_a_note_type_change_in_anki_is_changed_back(world: World):
    await world.sync()
    nid = world.nid()
    world.backend.retype(nid, "Basic with Extra")

    await world.sync()

    assert world.backend.note(nid).model == "Basic"


@pytest.mark.asyncio
@on(*REAL)
async def test_inv1_an_edited_card_in_the_queue_stays_queued_with_its_home_deck(world: World):
    """Companion: Arete::Queue is a view; syncing an edit must not take the card out of it."""
    backend = world.real
    await world.sync()
    nid = world.nid()
    backend.queue([nid])
    edit_card(world.md, _set(Back="An edited answer."))

    await world.sync()

    note = backend.note(nid)
    assert note.fields["Back"] == "<p>An edited answer.</p>", "the edit was sent"
    assert [(c.deck, c.home_deck) for c in note.cards] == [("Arete::Queue", HOME)]


# ---------------------------------------------------------------------------
# Invariant 2: the Arete id is the identity, whichever backend runs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@on(*ALL)
async def test_inv2_a_dangling_nid_is_resolved_by_the_arete_id(world: World):
    await world.sync()
    nid = world.nid()
    edit_vault(world.md, lambda t: re.sub(r"nid: '?\d+'?", "nid: '1111111111111'", t))

    await world.sync()

    assert [n.nid for n in world.backend.notes()] == [nid]
    assert vault_cards(world.vault)["card.md#1"]["nid"] == str(nid)


@pytest.mark.asyncio
@on(*ALL)
async def test_inv2_a_card_whose_text_changed_is_found_by_its_id(world: World):
    """The card lost its nid (copied, or the crash window of design 3.2) and was edited."""
    await world.sync()
    nid = world.nid()
    edit_card(world.md, _set(anki=None, Front="Reworded question?"))
    assert "nid" not in world.md.read_text(encoding="utf-8")

    await world.sync()

    assert [n.nid for n in world.backend.notes()] == [nid]


COPY_ID = "arete_01J00000000000000000000077"


@pytest.mark.asyncio
@pytest.mark.parametrize("touch_original", [False, True], ids=["original-cached", "both-fresh"])
@on(*ALL)
async def test_inv2_two_cards_declaring_one_arete_id_are_both_refused(
    world: World, touch_original: bool
):
    """A copied card keeps its original's id: sending either would claim the other's note.

    Both are refused with an error, neither note changes, and a confirmed prune in the same
    run keeps both notes, because both declared nids stay in the inventory.
    """
    copy = world.vault / "copy.md"
    copy.write_text(
        CARD.replace(ID, COPY_ID).replace("What does the vault hold?", "A copied question?"),
        encoding="utf-8",
    )
    await world.sync()
    before = {n.nid: (n.model, n.fields, sorted(n.tags)) for n in world.backend.notes()}
    assert len(before) == 2
    edit_vault(copy, lambda t: t.replace(COPY_ID, ID))
    if touch_original:
        edit_vault(world.md, lambda t: t)

    stats, updates = await world.sync(prune=True, confirm_prune=True)

    assert updates == [], "neither card reached Anki"
    assert stats.total_errors == 2
    after = {n.nid: (n.model, n.fields, sorted(n.tags)) for n in world.backend.notes()}
    assert after == before


# ---------------------------------------------------------------------------
# Invariant 4: Arete never touches scheduling.
# ---------------------------------------------------------------------------


async def _reviewed(world: World) -> tuple[int, dict[str, Any]]:
    """Sync, review every card twice, and return the note id and the scheduling snapshot."""
    backend = world.real
    await world.sync()
    backend.review_everything()
    return world.nid(), backend.scheduling()


def _without(snap: dict[str, Any], *columns: str) -> dict[str, Any]:
    cards = {
        cid: {k: v for k, v in c.items() if k not in columns} for cid, c in snap["cards"].items()
    }
    return {"cards": cards, "revlog": snap["revlog"]}


@pytest.mark.asyncio
@on(*REAL)
async def test_inv4_a_field_change_keeps_scheduling(world: World):
    nid, before = await _reviewed(world)
    assert before["revlog"] == 2 and next(iter(before["cards"].values()))["reps"] == 2
    edit_card(world.md, _set(Back="A new answer."))

    await world.sync()

    assert world.backend.note(nid).fields["Back"] == "<p>A new answer.</p>", "the edit was sent"
    assert world.real.scheduling() == before


@pytest.mark.asyncio
@on(*REAL)
async def test_inv4_a_tag_change_keeps_scheduling(world: World):
    nid, before = await _reviewed(world)
    edit_card(world.md, _set(tags=["another"]))

    await world.sync()

    assert "another" in world.backend.note(nid).tags, "the edit was sent"
    assert world.real.scheduling() == before


@pytest.mark.asyncio
@on(*REAL)
async def test_inv4_a_deck_change_keeps_scheduling(world: World):
    nid, before = await _reviewed(world)
    edit_card(world.md, _set(deck="Inv::Moved"))

    await world.sync()

    assert [c.deck for c in world.backend.note(nid).cards] == ["Inv::Moved"], "the edit was sent"
    after = world.real.scheduling()
    assert _without(after, "did") == _without(before, "did")


@pytest.mark.asyncio
@on(*REAL)
async def test_inv4_a_note_type_change_that_keeps_the_cards_keeps_scheduling(world: World):
    nid, before = await _reviewed(world)
    edit_card(world.md, _set(model="Basic with Extra", **{"Back Extra": "More."}))

    stats, _ = await world.sync()

    assert stats.total_errors == 0
    assert world.backend.note(nid).model == "Basic with Extra"
    assert world.real.scheduling() == before


@pytest.mark.asyncio
@on(*REAL)
async def test_inv4_a_deck_change_of_a_queued_card_keeps_scheduling_and_the_queue(world: World):
    """The card stays in Arete::Queue; only its home deck (`odid`) becomes the new deck."""
    backend = world.real
    nid, _ = await _reviewed(world)
    queue = backend.queue([nid])
    before = backend.scheduling()
    assert {c["did"] for c in before["cards"].values()} == {queue}
    edit_card(world.md, _set(deck="Inv::Moved"))

    await world.sync()

    assert [c.home_deck for c in backend.note(nid).cards] == ["Inv::Moved"], "the edit was sent"
    after = backend.scheduling()
    assert _without(after, "odid") == _without(before, "odid")
    assert {c["odid"] for c in after["cards"].values()} == {backend.deck_id("Inv::Moved")}


@pytest.mark.asyncio
@on(*REAL)
async def test_inv4_a_field_change_of_a_queued_card_keeps_scheduling(world: World):
    backend = world.real
    nid, _ = await _reviewed(world)
    backend.queue([nid])
    before = backend.scheduling()
    edit_card(world.md, _set(Back="A new answer."))

    await world.sync()

    assert backend.note(nid).fields["Back"] == "<p>A new answer.</p>", "the edit was sent"
    assert backend.scheduling() == before


# ---------------------------------------------------------------------------
# Invariant 6: nothing is deleted without confirmation.
#
# Already covered elsewhere:
#   tests/e2e/test_local_sync.py::test_an_unconfirmed_prune_returns_the_preview_and_deletes_nothing
#   tests/e2e/test_local_sync.py::test_a_conversion_that_would_delete_cards_is_refused (direct)
#   tests/application/sync/test_pipeline_prune.py: unconfirmed off a terminal, --force does not
#     confirm, the terminal prompt decides, a dry run deletes nothing, a partial run skips prune
#   tests/infrastructure/adapters/anki_connect/test_adapter.py::
#     test_sync_refuses_a_conversion_that_would_delete_cards (mocked transport)
#   tests/interface/test_cli_sync.py::test_only_yes_confirms_a_prune
#   tests/interface/test_path_containment.py: MCP and HTTP pass the confirmation through
# ---------------------------------------------------------------------------


class _Crash(RuntimeError):
    """Stands for the process dying between stage 3 (Anki and the cache) and stage 4."""


async def _crash_before_write_back(world: World) -> int:
    """A first sync that creates the note and caches it, then dies before writing the nid."""
    with patch.object(VaultService, "apply_updates", side_effect=_Crash):
        with pytest.raises(_Crash):
            await world.sync()
    assert vault_cards(world.vault)["card.md#1"]["nid"] is None, "the nid never reached the vault"
    return world.nid()


@pytest.mark.asyncio
@on(*ALL)
async def test_inv6_after_a_crash_the_prune_preview_does_not_list_the_vaults_note(world: World):
    nid = await _crash_before_write_back(world)

    stats, _ = await world.sync(prune=True)

    assert stats.prune is not None and stats.prune.status in ("unconfirmed", "clean")
    assert nid not in [n.nid for n in stats.prune.notes]


@pytest.mark.asyncio
@on(*ALL)
async def test_inv6_after_a_crash_a_confirmed_prune_keeps_the_vaults_note(world: World):
    nid = await _crash_before_write_back(world)

    await world.sync(prune=True, confirm_prune=True)

    assert nid in [n.nid for n in world.backend.notes()]


CLOZE_3 = CARD.replace("model: Basic", "model: Cloze").replace(
    "  Front: What does the vault hold?\n  Back: The vault's answer.\n",
    "  Text: '{{c1::a}} {{c2::b}} {{c3::c}}'\n  Back Extra: x\n",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ALL)
async def test_inv6_a_conversion_that_would_delete_cards_is_refused(name: str, tmp_path: Path):
    """Cloze with 3 cards -> Basic keeps one card; the other two and their reviews would go."""
    world = _world(name, tmp_path, CLOZE_3)
    try:
        await world.sync()
        nid = world.nid()
        assert len(world.backend.note(nid).cards) == 3
        edit_card(world.md, _set(model="Basic", Text=None, Front="Q", Back="A"))

        stats, updates = await world.sync()

        assert stats.total_errors == 1
        assert [u.ok for u in updates] == [False]
        assert "would delete 2 of its 3 cards" in (updates[0].error or "")
        note = world.backend.note(nid)
        assert (note.model, len(note.cards)) == ("Cloze", 3)
    finally:
        world.backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ALL)
async def test_inv3_a_note_converts_to_a_custom_cloze_type_on_every_backend(
    name: str, tmp_path: Path
):
    """Cloze is the type's kind, not its name: a 3-card note converts and keeps its cards."""
    world = _world(name, tmp_path, CLOZE_3)
    try:
        await world.sync()
        nid = world.nid()
        world.backend.copy_note_type("Cloze", "My Cloze")
        edit_card(world.md, _set(model="My Cloze"))

        stats, updates = await world.sync()

        assert stats.total_errors == 0, [u.error for u in updates]
        note = world.backend.note(nid)
        assert (note.model, len(note.cards)) == ("My Cloze", 3)
    finally:
        world.backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["fake", "addon"])
async def test_inv6_no_sync_without_prune_sends_a_delete(name: str, tmp_path: Path):
    """Create, update, a dangling nid, an Anki edit and a refused conversion: no delete sent."""
    world = _world(name, tmp_path)
    log: list[str] = []
    backend = world.backend
    assert isinstance(backend, (FakeBackend, AddonBackend))
    target = backend.fake if isinstance(backend, FakeBackend) else backend.host
    transport = target.http

    async def _logged(payload: dict) -> Any:
        log.append(payload["action"])
        log.extend(a["action"] for a in payload.get("params", {}).get("actions", []))
        return await transport(payload)

    try:
        with patch.object(target, "http", _logged):
            await world.sync()
            backend.set_field(world.nid(), "Back", "edited in Anki")
            edit_vault(world.md, lambda t: re.sub(r"nid: '?\d+'?", "nid: '1111111111111'", t))
            await world.sync()
            await world.sync(force=True)
        assert log, "the transport was not reached"
        assert not [a for a in log if re.search(r"delete|remove(?!Tags)|forget", a, re.I)]
    finally:
        backend.close()


# ---------------------------------------------------------------------------
# Invariant 5: a sync writes only ids into the vault.
# ---------------------------------------------------------------------------

# Frontmatter the way a person writes it: comments, a key order of their own, quoting,
# block scalars of every style, a blank line, flow lists, a date.
NEW_CARDS = """---
# A comment the owner wrote.
deck: Inv::Five   # trailing comment
arete: true
aliases: [One, "Two"]
created: 2024-01-02
tags:
  - 'quoted tag'
  - plain

cards:
  - Front: "Double-quoted: question?"
    Back: 'Single-quoted answer'
  - Front: |
      Literal block
      kept as written.
    Back: >-
      Folded block
      on two lines.
---

# Body

Text with trailing spaces
and a final line without a newline."""

HEAL = f"""---
arete: true
deck: Inv::Five
cards:
- id: {ID}
  Front: Healed question
  Back: Healed answer
---

Body.
"""

DANGLING = """---
arete: true
deck: Inv::Five
cards:
- id: arete_01J00000000000000000000043
  Front: Dangling question
  Back: Dangling answer
  anki:
    nid: '1111111111111'
---

Body.
"""

UNTOUCHED = """---
arete: true
deck: Inv::Five
cards:
- id: arete_01J00000000000000000000044
  Front: Untouched question
  Back: Untouched answer
  anki:
    nid: '{nid}'
---

Body.
"""

INV5_FILES = ("new.md", "heal.md", "dangling.md", "untouched.md")
ID_LINE = re.compile(r"^\s*(- )?(id|anki|nid|cid):")


def _split(text: str) -> tuple[list[str], str]:
    """Frontmatter lines and the body, exactly as stored."""
    lines = text.split("\n")
    end = next(i for i, line in enumerate(lines[1:], start=1) if line.rstrip() == "---")
    return lines[1:end], "\n".join(lines[end + 1 :])


def _scrub(value: Any) -> Any:
    """Parsed frontmatter without the ids a sync may write and the parser's line numbers."""
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: _scrub(v) for k, v in value.items() if k not in ("__line__", "id")}
    if isinstance(out.get("anki"), dict):
        out["anki"] = {k: v for k, v in out["anki"].items() if k not in ("nid", "cid")}
        if not out["anki"]:
            del out["anki"]
    return out


async def _inv5_vault(name: str, root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Sync the four-file vault once through one backend; each file's text before and after."""
    vault = root / "vault"
    vault.mkdir()
    backend = make_backend(name, root)
    try:
        backend.add_note("Basic", "Inv::Five", {"Front": "<p>Healed question</p>"}, [ID])
        kept = backend.add_note(
            "Basic",
            "Inv::Five",
            {"Front": "<p>Untouched question</p>", "Back": "<p>Untouched answer</p>"},
            ["arete_01J00000000000000000000044"],
        )
        texts = [NEW_CARDS, HEAL, DANGLING, UNTOUCHED.format(nid=kept)]
        before = dict(zip(INV5_FILES, texts, strict=True))
        for filename, text in before.items():
            (vault / filename).write_text(text, encoding="utf-8")
        stats, _ = await run_sync(backend, sync_config(root, vault, backend))
        assert stats.total_errors == 0
        after = {f: (vault / f).read_text(encoding="utf-8") for f in INV5_FILES}
        assert vault_cards(vault)["heal.md#1"]["nid"], "the healed card got its nid"
        return before, after
    finally:
        backend.close()


_INV5: dict[str, tuple[dict[str, str], dict[str, str]]] = {}


async def inv5(name: str, tmp_path_factory) -> tuple[dict[str, str], dict[str, str]]:
    if name not in _INV5:
        _INV5[name] = await _inv5_vault(name, tmp_path_factory.mktemp(f"inv5-{name}"))
    return _INV5[name]


REDUMP = (
    "write-back re-dumps the whole frontmatter (vault_service.apply_updates -> "
    "rebuild_markdown_with_frontmatter); observed: "
)
DECK_AS_BLOCK = "`deck: Inv::Five` rewritten as the block scalar `deck: |-` / `  Inv::Five`"
NEW_MD_TEXT = (
    "new.md is re-dumped twice, at id assignment (pipeline stage 1.5) and at write-back; "
    'observed: both comments dropped, `aliases: [One, "Two"]` turned into a block list, '
    "the quotes on 'quoted tag' and both card values dropped, the blank line before `cards:` "
    "dropped, the list indent 2 -> 0, `Back: >-` folded into one plain line, `Front: |` "
    "turned into `|-`, and " + DECK_AS_BLOCK
)
INV5_TEXT_XFAIL = {
    **{(b, "new.md"): NEW_MD_TEXT for b in ALL},
    **{(b, "heal.md"): REDUMP + DECK_AS_BLOCK for b in ALL},
    **{(b, "dangling.md"): REDUMP + DECK_AS_BLOCK for b in ALL},
}
INV5_PARSED_XFAIL = {
    (b, "new.md"): "the literal block `Front: |` (clip: the value ends in a newline) is "
    "re-dumped as `|-`, which strips it; observed Front 'Literal block\\nkept as written.\\n' "
    "-> 'Literal block\\nkept as written.'"
    for b in ALL
}


def _cases(xfails: dict[tuple[str, str], str]):
    return [
        pytest.param(b, f, marks=pytest.mark.xfail(strict=True, reason=xfails[b, f]))
        if (b, f) in xfails
        else pytest.param(b, f)
        for b in ALL
        for f in INV5_FILES
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("backend", "filename"), _cases({}))
async def test_inv5_the_body_bytes_are_unchanged(backend: str, filename: str, tmp_path_factory):
    before, after = await inv5(backend, tmp_path_factory)
    assert _split(after[filename])[1] == _split(before[filename])[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(("backend", "filename"), _cases(INV5_PARSED_XFAIL))
async def test_inv5_the_parsed_frontmatter_differs_only_in_ids(
    backend: str, filename: str, tmp_path_factory
):
    before, after = await inv5(backend, tmp_path_factory)
    assert _scrub(parse_frontmatter(after[filename])[0]) == _scrub(
        parse_frontmatter(before[filename])[0]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("backend", "filename"), _cases(INV5_TEXT_XFAIL))
async def test_inv5_the_frontmatter_text_gains_only_id_lines(
    backend: str, filename: str, tmp_path_factory
):
    """Comments, key order, quoting, block-scalar style and blank lines are kept as written."""
    before, after = await inv5(backend, tmp_path_factory)
    diff = difflib.ndiff(_split(before[filename])[0], _split(after[filename])[0])
    changed = [d for d in diff if d[:2] in ("- ", "+ ")]
    assert [d for d in changed if not (d.startswith("+ ") and ID_LINE.match(d[2:]))] == []
