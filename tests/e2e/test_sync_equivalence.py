"""What each backend leaves in Anki after a sync, and every way the backends differ.

A case is a vault plus the steps between its syncs: the three fixture vaults (synced twice,
fresh then warm) and four small scenarios built here, each aimed at one row of the census of
differences in the shared-sync design (`/tmp/arete-audit/design-shared-sync.md`, section 1a).
`sync_harness.snapshot` reads each case's end state on each backend.

1. Golden: each snapshot equals the JSON under `tests/fixtures/sync_golden/`, so a change to
   what a backend writes, or to the ids it reports, fails here. Regenerate after a change you
   meant, then read the JSON diff before committing it:
   `REGEN_SYNC_GOLDEN=1 uv run pytest tests/e2e/test_sync_equivalence.py -k golden`
2. Named differences: two backends' snapshots differ only at paths claimed by the named,
   expected differences of that case, and each named difference is still there. When the
   shared sync logic removes one, its test fails until the name is deleted from `EXPECTED`.
3. Invariant 3 (both backends behave the same): the strict xfail that holds once `EXPECTED`
   is empty for the direct/AnkiConnect pairs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.sync_harness import (
    BACKENDS,
    Backend,
    edit_vault,
    make_backend,
    run_sync,
    snapshot,
    sync_config,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "sync_golden"
REGENERATE = os.environ.get("REGEN_SYNC_GOLDEN") == "1"

FIXTURE_VAULTS = {
    "corpus": FIXTURES / "corpus",
    "scenario_basic": FIXTURES / "vault_scenarios" / "basic",
    "scenario_rich_text": FIXTURES / "vault_scenarios" / "rich_text",
}


def arete_id(n: int) -> str:
    """A fixed, valid Arete id (`arete_` + 26 Crockford base32 characters)."""
    return f"arete_01J{n:023d}"


def _note(deck: str, cards: str, model: str = "Basic") -> str:
    return f"---\narete: true\ndeck: {deck}\nmodel: {model}\ncards:\n{cards}---\n\nBody.\n"


SCENARIO_VAULTS: dict[str, dict[str, str]] = {
    "dangling_nid": {
        "card.md": _note("S::Dangling", f"- id: {arete_id(1)}\n  Front: Q dangling\n  Back: A\n"),
    },
    "same_front_twice": {
        "twins.md": _note(
            "S::Twins",
            f"- id: {arete_id(2)}\n  Front: Same question\n  Back: First answer\n"
            f"- id: {arete_id(3)}\n  Front: Same question\n  Back: Second answer\n",
        ),
    },
    "field_name_case": {
        "case.md": _note(
            "S::Case",
            f"- id: {arete_id(4)}\n  front: lower-case front\n  back: lower-case back\n"
            "  Back Extra: extra\n",
            model="Basic with Extra",
        ),
    },
    "split_note": {
        "split.md": _note(
            "S::Home",
            f"- id: {arete_id(5)}\n  Text: '{{{{c1::one}}}} and {{{{c2::two}}}}'\n"
            "  Back Extra: first\n",
            model="Cloze",
        ),
    },
}

Sync = Callable[[], Awaitable[None]]


async def _sync_twice(vault: Path, backend: Backend, sync: Sync) -> None:
    await sync()
    await sync()


async def _dangling_nid(vault: Path, backend: Backend, sync: Sync) -> None:
    """Sync, then the vault's nid points at a note Anki never issued, then sync."""
    await sync()
    edit_vault(vault / "card.md", lambda t: re.sub(r"nid: '?\d+'?", "nid: '1111111111111'", t))
    await sync()


async def _split_note(vault: Path, backend: Backend, sync: Sync) -> None:
    """Sync, move the cloze note's second card in Anki, edit the vault, sync."""
    await sync()
    backend.move_card(backend.notes()[0].cards[1].cid, "S::Elsewhere")
    edit_vault(vault / "split.md", lambda t: t.replace("Back Extra: first", "Back Extra: edited"))
    await sync()


STEPS: dict[str, Callable[[Path, Backend, Sync], Awaitable[None]]] = {
    "dangling_nid": _dangling_nid,
    "same_front_twice": _sync_twice,
    "field_name_case": _sync_twice,
    "split_note": _split_note,
}
CASES = sorted([*FIXTURE_VAULTS, *SCENARIO_VAULTS])


def _write_vault(case: str, vault: Path) -> None:
    if case in FIXTURE_VAULTS:
        shutil.copytree(FIXTURE_VAULTS[case], vault)
        return
    vault.mkdir()
    for filename, text in SCENARIO_VAULTS[case].items():
        (vault / filename).write_text(text, encoding="utf-8")


async def run_case(case: str, backend_name: str, root: Path) -> dict[str, Any]:
    """Build the case's vault under `root`, run its steps through one backend, snapshot."""
    vault = root / "vault"
    _write_vault(case, vault)
    backend = make_backend(backend_name, root)
    runs: list[list] = []

    async def sync() -> None:
        _, updates = await run_sync(backend, sync_config(root, vault, backend))
        runs.append(updates)

    try:
        await STEPS.get(case, _sync_twice)(vault, backend, sync)
        return snapshot(backend, vault, runs)
    finally:
        backend.close()


_SNAPSHOTS: dict[tuple[str, str], dict[str, Any]] = {}


async def case_snapshot(case: str, backend: str, tmp_path_factory) -> dict[str, Any]:
    """The case's snapshot on one backend, computed once per session (it is deterministic)."""
    if (case, backend) not in _SNAPSHOTS:
        root = tmp_path_factory.mktemp(f"{case}-{backend}")
        _SNAPSHOTS[case, backend] = await run_case(case, backend, root)
    return _SNAPSHOTS[case, backend]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("case", CASES)
async def test_golden_state_after_sync(case: str, backend: str, tmp_path_factory):
    got = await case_snapshot(case, backend, tmp_path_factory)
    path = GOLDEN / f"{case}.{backend}.json"
    if REGENERATE:
        GOLDEN.mkdir(exist_ok=True)
        text = json.dumps(got, indent=2, ensure_ascii=False, sort_keys=True)
        path.write_text(text + "\n", encoding="utf-8")
    assert got == json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Named differences
# ---------------------------------------------------------------------------


def flatten(snap: dict[str, Any]) -> dict[str, Any]:
    """Path -> value; lists of notes by position, runs by card key, tags and decks whole."""
    flat: dict[str, Any] = {}
    for key, notes in snap["notes"].items():
        for i, note in enumerate(notes):
            prefix = f"notes/{key}/{i}"
            flat[f"{prefix}/model"] = note["model"]
            flat[f"{prefix}/tags"] = note["tags"]
            flat[f"{prefix}/decks"] = note["decks"]
            flat.update((f"{prefix}/fields/{k}", v) for k, v in note["fields"].items())
    for key, ids in snap["vault_ids"].items():
        flat.update((f"vault_ids/{key}/{k}", v) for k, v in ids.items())
    for r, run in enumerate(snap["runs"]):
        for row in run:
            flat.update((f"runs/{r}/{row['card']}/{k}", v) for k, v in row.items() if k != "card")
    return flat


def differing_paths(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    fa, fb = flatten(a), flatten(b)
    missing = object()
    return sorted(p for p in fa.keys() | fb.keys() if fa.get(p, missing) != fb.get(p, missing))


@dataclass(frozen=True)
class Difference:
    """A known way two backends differ: what it is, its census row, the paths it explains."""

    name: str
    census: str
    paths: str  # a regex over `flatten` paths

    def claims(self, path: str) -> bool:
        return re.search(self.paths, path) is not None


SOURCE_FIELD = Difference(
    "AnkiConnect adds `_obsidian_source` to the note type and fills it; direct drops it",
    "1a #11",
    r"/fields/_obsidian_source$",
)
CID_WRITE_BACK = Difference(
    "AnkiConnect returns the first card id on create and heal and the vault stores it; "
    "direct returns no cid",
    "1a #18",
    r"(/new_cid|^vault_ids/[^/]+/cid)$",
)
DANGLING_DUPLICATE = Difference(
    "a nid Anki never issued: direct creates a second note, AnkiConnect heals by the id tag",
    "1a #2",
    r"^notes/[^/]+/1/",
)
SPLIT_LEFT_SPLIT = Difference(
    "a cloze note whose cards sit in two decks: AnkiConnect moves every card home, direct "
    "compares only the first card's deck and leaves the note split",
    "1a #15",
    r"^notes/split\.md#1/0/decks$",
)
FIELD_CASE = Difference(
    "a card field named in another case (`front`): the add-on fills `Front`, direct and the "
    "fake leave it empty and still report ok",
    "1a #10b",
    r"^notes/case\.md#1/0/fields/(Front|Back)$",
)
DUPLICATE_REFUSED = Difference(
    "two cards with the same first field in one deck: the add-on refuses the second card's "
    "addNote as a duplicate; direct and the fake add both",
    "1a #8",
    r"^runs/0/twins\.md#2/",
)
CONTENT_ADOPTS_OTHER_CARD = Difference(
    "next run the refused card is healed by content onto the first card's note, which then "
    "carries the second card's fields and id: two vault cards share one note, and the first "
    "card's text is gone from Anki",
    "1a #7",
    r"^(notes/twins\.md#[12]/|runs/[01]/twins\.md#1/|runs/1/twins\.md#2/|vault_ids/twins\.md#1/)",
)

# What the AnkiConnect adapter does differently from the direct one, on any AnkiConnect side.
_AC_VS_DIRECT = {
    "dangling_nid": [SOURCE_FIELD, CID_WRITE_BACK, DANGLING_DUPLICATE],
    "split_note": [SOURCE_FIELD, CID_WRITE_BACK, SPLIT_LEFT_SPLIT],
}
EXPECTED: dict[tuple[str, str], dict[str, list[Difference]]] = {
    ("direct", "fake"): {
        case: _AC_VS_DIRECT.get(case, [SOURCE_FIELD, CID_WRITE_BACK]) for case in CASES
    },
    ("direct", "addon"): {
        **{case: _AC_VS_DIRECT.get(case, [SOURCE_FIELD, CID_WRITE_BACK]) for case in CASES},
        "field_name_case": [SOURCE_FIELD, CID_WRITE_BACK, FIELD_CASE],
        "same_front_twice": [
            SOURCE_FIELD,
            CID_WRITE_BACK,
            DUPLICATE_REFUSED,
            CONTENT_ADOPTS_OTHER_CARD,
        ],
    },
    # Where the fake is not the add-on: it has no duplicate check and matches field names
    # case-sensitively. Not a backend difference; it says what the fake cannot show.
    ("fake", "addon"): {
        **{case: [] for case in CASES},
        "field_name_case": [FIELD_CASE],
        "same_front_twice": [DUPLICATE_REFUSED, CONTENT_ADOPTS_OTHER_CARD],
    },
}
PAIRS = sorted(EXPECTED)


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", PAIRS, ids="-vs-".join)
@pytest.mark.parametrize("case", CASES)
async def test_backends_differ_only_in_the_named_ways(
    case: str, pair: tuple[str, str], tmp_path_factory
):
    a = await case_snapshot(case, pair[0], tmp_path_factory)
    b = await case_snapshot(case, pair[1], tmp_path_factory)
    paths = differing_paths(a, b)
    expected = EXPECTED[pair][case]

    unnamed = [p for p in paths if not any(d.claims(p) for d in expected)]
    gone = [d.name for d in expected if not any(d.claims(p) for p in paths)]
    assert not unnamed, f"{pair} differ where no named difference explains it: {unnamed}"
    assert not gone, f"no longer different, delete from EXPECTED[{pair}][{case!r}]: {gone}"


@pytest.mark.xfail(
    strict=True,
    reason="Invariant 3 does not hold: every case differs in `_obsidian_source` (census 1a "
    "#11) and the cid write-back (#18), and the scenarios in the dangling nid (#2), the split "
    "cloze note (#15) and, on the add-on, field-name case (#10b), the duplicate first field "
    "(#8) and the adoption by content (#7). See EXPECTED.",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("ac", ["fake", "addon"])
async def test_invariant_3_both_backends_leave_the_same_state(ac: str, tmp_path_factory):
    differing = {}
    for case in CASES:
        a = await case_snapshot(case, "direct", tmp_path_factory)
        b = await case_snapshot(case, ac, tmp_path_factory)
        if paths := differing_paths(a, b):
            differing[case] = paths
    assert not differing
