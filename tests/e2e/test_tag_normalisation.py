"""A vault tag is compared as Anki stores it, so a sync after a sync changes nothing.

Anki splits a tag at spaces (`Cognitive Science` is stored as `Cognitive` and `Science`)
and rewrites some characters (`domain.identity.anki_tags`, pinned against the anki
library). A vault declaring such a tag must converge: created once, then left alone.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter
from tests.e2e.sync_harness import BACKENDS, edit_vault, make_backend, run_sync, sync_config

CARD = "arete_01J00000000000000000000501"


def _file(tags: list[str]) -> str:
    """The tags sit on the card, after its fields, so a retag never moves the card's line."""
    return rebuild_markdown_with_frontmatter(
        {
            "arete": True,
            "deck": "Tags::Deck",
            "model": "Basic",
            "cards": [{"id": CARD, "Front": "Tag question", "Back": "Answer", "tags": tags}],
        },
        "\nBody.\n",
    )


def _retag(md: Path, tags: list[str]) -> None:
    def edit(text: str) -> str:
        meta, body = parse_frontmatter(text)
        meta["cards"][0]["tags"] = tags
        return rebuild_markdown_with_frontmatter(meta, body)

    edit_vault(md, edit)


@pytest.fixture(params=BACKENDS)
def world(request, tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    backend = make_backend(request.param, tmp_path)
    yield tmp_path, vault, backend
    backend.close()


async def _sync(world, **config):
    root, vault, backend = world
    stats, [update] = await run_sync(backend, sync_config(root, vault, backend, **config))
    assert update.ok and stats.total_errors == 0, update.error
    return update


def _tags(backend) -> set[str]:
    [note] = backend.notes()
    return {t.casefold() for t in note.tags} - {CARD.casefold()}


@pytest.mark.asyncio
async def test_a_spaced_tag_is_created_once_then_left_alone(world):
    _, vault, backend = world
    (vault / "t.md").write_text(_file(["Cognitive Science", "Plain"]), encoding="utf-8")

    created = await _sync(world)
    assert _tags(backend) == {"cognitive", "science", "plain"}

    dry = await _sync(world, dry_run=True)
    again = await _sync(world)

    assert created.changed and not dry.changed and not again.changed
    assert _tags(backend) == {"cognitive", "science", "plain"}


@pytest.mark.asyncio
async def test_a_tag_with_a_no_break_space_is_one_tag_and_left_alone(world):
    """Anki keeps a no-break space inside a tag; reading the note must not split it."""
    _, vault, backend = world
    (vault / "t.md").write_text(_file(["no break", "Parent::Child"]), encoding="utf-8")

    await _sync(world)
    again = await _sync(world)

    assert not again.changed and _tags(backend) == {"no break", "parent::child"}


@pytest.mark.asyncio
async def test_a_note_holding_the_parts_is_left_alone(world):
    _, vault, backend = world
    (vault / "t.md").write_text(_file(["Cognitive", "Science"]), encoding="utf-8")
    await _sync(world)
    _retag(vault / "t.md", ["Cognitive Science"])

    update = await _sync(world)

    assert not update.changed and _tags(backend) == {"cognitive", "science"}


@pytest.mark.asyncio
async def test_removing_the_spaced_tag_removes_both_parts(world):
    _, vault, backend = world
    (vault / "t.md").write_text(_file(["Cognitive Science", "Plain"]), encoding="utf-8")
    await _sync(world)
    _retag(vault / "t.md", ["Plain"])

    update = await _sync(world)

    assert update.changed and _tags(backend) == {"plain"}


@pytest.mark.asyncio
async def test_a_part_declared_alone_still_matches(world):
    _, vault, backend = world
    (vault / "t.md").write_text(_file(["Cognitive Science"]), encoding="utf-8")
    await _sync(world)
    _retag(vault / "t.md", ["Science"])

    update = await _sync(world)
    after = await _sync(world)

    assert update.changed and not after.changed and _tags(backend) == {"science"}


_PIECE = st.text(alphabet=st.sampled_from("aZé \t 　:,_"), min_size=1, max_size=8)


@pytest.mark.parametrize("backend_name", BACKENDS)
@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(tags=st.lists(_PIECE.filter(str.strip), max_size=4))
def test_after_one_sync_a_second_plans_no_tag_change(backend_name, tags):
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        vault = root / "vault"
        vault.mkdir()
        (vault / "t.md").write_text(_file(tags), encoding="utf-8")
        backend = make_backend(backend_name, root)
        try:
            world = (root, vault, backend)
            asyncio.run(_sync(world))
            dry = asyncio.run(_sync(world, dry_run=True))
            assert not dry.changed, (tags, backend.notes()[0].tags)
        finally:
            backend.close()
