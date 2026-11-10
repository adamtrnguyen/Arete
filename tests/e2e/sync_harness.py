"""Run a whole sync through one backend, then read back what Anki holds in a comparable form.

Three backends, each behind the real pipeline (`execute_sync`) with only the bridge swapped:

| name     | adapter                 | Anki side                                                 |
|----------|-------------------------|-----------------------------------------------------------|
| `direct` | `AnkiDirectAdapter`     | a real collection in `tmp_path`                           |
| `fake`   | `AnkiConnectAdapter`    | `FakeAnkiPlus`, the in-memory add-on of the unit tests     |
| `addon`  | `AnkiConnectAdapter`    | the add-on's own handler over a real collection (`addon_host`) |

`snapshot` turns the end state into plain JSON: per vault card, the note(s) Anki holds for it
(model, fields, tags, deck of each card), the ids the vault holds for it, and the `UpdateItem`s
of each run. Note ids, card ids and generated Arete ids are replaced by what they point at, so
two backends, or two runs on one machine, give equal snapshots when they did the same thing.
"""

from __future__ import annotations

import contextlib
import os
import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from anki.collection import Collection
from anki.scheduler.v3 import CardAnswer

from arete.application.config import AppConfig
from arete.application.sync.pipeline import RunStats
from arete.application.utils.text import parse_frontmatter
from arete.composition.orchestrator import execute_sync
from arete.domain.interfaces import AnkiBridge
from arete.domain.models import UpdateItem
from arete.domain.note_types import map_note_type_fields
from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter
from arete.infrastructure.adapters.anki_direct import AnkiDirectAdapter
from tests.e2e.addon_host import AddonHost
from tests.e2e.conftest import PROFILE, make_anki_base, open_collection
from tests.infrastructure.adapters.anki_connect.fake_anki import FakeAnki

BACKENDS = ("direct", "fake", "addon")

# The card columns that hold scheduling state, and the deck columns beside them.
SCHEDULING_COLUMNS = (
    "type",
    "queue",
    "due",
    "ivl",
    "factor",
    "reps",
    "lapses",
    "left",
    "odue",
    "odid",
    "flags",
)

# Stock Anki's note types, as a fresh collection has them, plus the "Basic with Extra" that
# `make_anki_base` adds. No `_obsidian_source`: the AnkiConnect adapter adds it.
STOCK_MODELS = {
    "Basic": ["Front", "Back"],
    "Cloze": ["Text", "Back Extra"],
    "Basic with Extra": ["Front", "Back", "Back Extra"],
}


@dataclass(frozen=True)
class HeldCard:
    cid: int
    ord: int
    deck: str  # the deck the card is in now, a filtered deck while it is queued
    home_deck: str  # the deck it returns to: `odid` while queued, else the same as `deck`


@dataclass
class HeldNote:
    nid: int
    model: str
    fields: dict[str, str]
    tags: list[str]
    cards: list[HeldCard]  # in `ord` order


class FakeAnkiPlus(FakeAnki):
    """`FakeAnki` with stock note types, `modelTemplates`, `changeNoteType` and cloze cards.

    A field added to a note type is added, empty, to that type's notes, as Anki does.

    It still has no duplicate check and matches field names case-sensitively; the add-on
    does both (`arete_ankiconnect/__init__.py:createNote`). The `addon` backend has them.
    """

    def __init__(self):
        """Start as a fresh collection does: the stock note types, no `_obsidian_source`."""
        super().__init__({name: list(fields) for name, fields in STOCK_MODELS.items()})

    def _a_modelFieldAdd(self, modelName, fieldName):
        super()._a_modelFieldAdd(modelName, fieldName)
        for note in self.notes.values():
            if note["model"] == modelName:
                note["fields"].setdefault(fieldName, "")

    def _a_modelTemplates(self, modelName):
        if modelName not in self.models:
            raise Exception(f"model was not found: {modelName}")
        return {"Card 1": {"Front": "", "Back": ""}}

    def _a_addNote(self, note):
        if note["deckName"] not in self.decks:
            raise Exception("deck was not found: {}".format(note["deckName"]))
        n_cards = 1
        if note["modelName"] == "Cloze":
            text = note["fields"].get("Text", "")
            n_cards = max(1, len(set(re.findall(r"\{\{c(\d+)::", text))))
        return self.add(note["fields"], note["deckName"], note["modelName"], note["tags"], n_cards)

    def _a_changeNoteType(self, notes, modelName, newFields):
        for nid in notes:
            note = self.notes[int(nid)]
            old = [note["fields"][name] for name in self.models[note["model"]]]
            new_names = self.models[modelName]
            note["fields"] = {
                name: old[src] if src >= 0 else ""
                for name, src in zip(new_names, newFields, strict=True)
            }
            note["model"] = modelName
        return True


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class Backend(ABC):
    """One way of reaching a collection: the adapter a sync uses, and direct access for tests."""

    name: str

    @abstractmethod
    def bridge(self) -> AnkiBridge:
        """A fresh adapter, as the composition root builds one per run."""

    @abstractmethod
    def notes(self) -> list[HeldNote]:
        """Every note Anki holds, by note id."""

    @abstractmethod
    def add_note(self, model: str, deck: str, fields: dict[str, str], tags: list[str]) -> int:
        """Add a note as a person would in Anki; its id."""

    @abstractmethod
    def set_field(self, nid: int, name: str, value: str) -> None: ...

    @abstractmethod
    def add_tag(self, nid: int, tag: str) -> None: ...

    @abstractmethod
    def move(self, nid: int, deck: str) -> None:
        """Move every card of the note, as Anki's browser does."""

    @abstractmethod
    def retype(self, nid: int, model: str) -> None:
        """Change the note type in place, cards kept."""

    @abstractmethod
    def move_card(self, cid: int, deck: str) -> None:
        """Move one card of a note, leaving its siblings where they are."""

    @abstractmethod
    def close(self) -> None:
        """Release what the backend holds open."""

    def note(self, nid: int) -> HeldNote:
        return next(n for n in self.notes() if n.nid == nid)


class CollectionBackend(Backend):
    """A backend with a real collection behind it; scheduling can be read and written."""

    @abstractmethod
    def collection(self) -> contextlib.AbstractContextManager[Collection]: ...

    def notes(self) -> list[HeldNote]:
        with self.collection() as col:
            return [_held_note(col, nid) for nid in sorted(col.find_notes(""))]

    def add_note(self, model: str, deck: str, fields: dict[str, str], tags: list[str]) -> int:
        with self.collection() as col:
            note_type = col.models.by_name(model)
            assert note_type is not None
            note = col.new_note(note_type)
            for name, value in fields.items():
                note[name] = value
            note.tags = list(tags)
            col.add_note(note, col.decks.id(deck))  # type: ignore[arg-type]
            return int(note.id)

    def set_field(self, nid: int, name: str, value: str) -> None:
        with self.collection() as col:
            note = col.get_note(nid)  # type: ignore[arg-type]
            note[name] = value
            col.update_note(note)

    def add_tag(self, nid: int, tag: str) -> None:
        with self.collection() as col:
            note = col.get_note(nid)  # type: ignore[arg-type]
            note.add_tag(tag)
            col.update_note(note)

    def move(self, nid: int, deck: str) -> None:
        with self.collection() as col:
            col.set_deck(col.card_ids_of_note(nid), col.decks.id(deck))  # type: ignore[arg-type]

    def move_card(self, cid: int, deck: str) -> None:
        with self.collection() as col:
            col.set_deck([cid], col.decks.id(deck))  # type: ignore[arg-type]

    def retype(self, nid: int, model: str) -> None:
        with self.collection() as col:
            note = col.get_note(nid)  # type: ignore[arg-type]
            old = note.note_type()
            new = col.models.by_name(model)
            assert old is not None and new is not None
            request = col.models.change_notetype_info(
                old_notetype_id=old["id"], new_notetype_id=new["id"]
            ).input
            request.note_ids.append(nid)
            request.new_fields[:] = map_note_type_fields(
                [f["name"] for f in old["flds"]], [f["name"] for f in new["flds"]]
            )
            col.models.change_notetype_of_notes(request)

    def review_everything(self) -> None:
        """Answer every card Good twice through the scheduler, so it has review history."""
        with self.collection() as col:
            for _ in range(2):
                for cid in sorted(col.find_cards("")):
                    card = col.get_card(cid)
                    card.start_timer()
                    states = col._backend.get_scheduling_states(cid)
                    col.sched.answer_card(
                        col.sched.build_answer(card=card, states=states, rating=CardAnswer.GOOD)
                    )

    def queue(self, nids: list[int], name: str = "Arete::Queue") -> int:
        """Put the notes' cards in a filtered deck, as `create_topo_deck` does; its id."""
        with self.collection() as col:
            did = col.decks.new_filtered(name)
            deck = col.decks.get(did)
            assert deck is not None
            deck["terms"] = [[" OR ".join(f"nid:{n}" for n in nids), 9999, 0]]
            deck["resched"] = True
            col.decks.save(deck)
            col.sched.rebuild_filtered_deck(did)
            return int(did)

    def scheduling(self) -> dict[str, Any]:
        """Per card id the scheduling columns and `did`, plus the revlog row count."""
        cols = ", ".join(SCHEDULING_COLUMNS)
        with self.collection() as col:
            rows = col.db.all(f"select id, did, {cols} from cards order by id")
            revlog = col.db.scalar("select count() from revlog")
        cards = {
            row[0]: dict(zip(("did", *SCHEDULING_COLUMNS), row[1:], strict=True)) for row in rows
        }
        return {"cards": cards, "revlog": revlog}

    def deck_id(self, name: str) -> int:
        with self.collection() as col:
            return int(col.decks.id(name))  # type: ignore[arg-type]


class DirectBackend(CollectionBackend):
    name = "direct"

    def __init__(self, anki_base: Path):
        """A collection the direct adapter opens and closes per batch, as with Anki closed."""
        self.anki_base = anki_base

    def bridge(self) -> AnkiBridge:
        return AnkiDirectAdapter(anki_base=self.anki_base)

    def close(self) -> None:
        """Nothing is held open: each access opens and closes the collection."""

    @contextlib.contextmanager
    def collection(self) -> Iterator[Collection]:
        col = open_collection(self.anki_base)
        try:
            yield col
        finally:
            col.close()


class AddonBackend(CollectionBackend):
    name = "addon"

    def __init__(self, anki_base: Path):
        """The add-on holds the collection open for the whole test, as a running Anki does."""
        self.host = AddonHost(anki_base / PROFILE / "collection.anki2")

    def bridge(self) -> AnkiBridge:
        adapter = AnkiConnectAdapter(url="http://addon-host:8765")
        adapter._http_request = self.host.http  # type: ignore[method-assign]
        return adapter

    @contextlib.contextmanager
    def collection(self) -> Iterator[Collection]:
        yield self.host.col

    def close(self) -> None:
        self.host.close()


class FakeBackend(Backend):
    name = "fake"

    def __init__(self):
        """An empty in-memory collection."""
        self.fake = FakeAnkiPlus()

    def bridge(self) -> AnkiBridge:
        adapter = AnkiConnectAdapter(url="http://fake-anki:8765")
        adapter._http_request = self.fake.http  # type: ignore[method-assign]
        return adapter

    def close(self) -> None:
        """Nothing is held open."""

    def notes(self) -> list[HeldNote]:
        out = []
        for nid in sorted(self.fake.notes):
            n = self.fake.notes[nid]
            cards = [
                HeldCard(cid, i, self.fake.card_deck[cid], self.fake.card_deck[cid])
                for i, cid in enumerate(n["cards"])
            ]
            out.append(HeldNote(nid, n["model"], dict(n["fields"]), sorted(n["tags"]), cards))
        return out

    def add_note(self, model: str, deck: str, fields: dict[str, str], tags: list[str]) -> int:
        return self.fake.add(fields, deck, model, tags)

    def set_field(self, nid: int, name: str, value: str) -> None:
        self.fake.notes[nid]["fields"][name] = value

    def add_tag(self, nid: int, tag: str) -> None:
        self.fake.notes[nid]["tags"].append(tag)

    def move(self, nid: int, deck: str) -> None:
        self.fake.decks.add(deck)
        for cid in self.fake.notes[nid]["cards"]:
            self.fake.card_deck[cid] = deck

    def move_card(self, cid: int, deck: str) -> None:
        self.fake.decks.add(deck)
        self.fake.card_deck[cid] = deck

    def retype(self, nid: int, model: str) -> None:
        note = self.fake.notes[nid]
        mapping = map_note_type_fields(self.fake.models[note["model"]], self.fake.models[model])
        self.fake._a_changeNoteType([nid], model, mapping)


def _held_note(col: Collection, nid: int) -> HeldNote:
    note = col.get_note(nid)  # type: ignore[arg-type]
    model = note.note_type()
    assert model is not None
    rows = col.db.all("select id, ord, did, odid from cards where nid = ? order by ord", nid)
    cards = [
        HeldCard(cid, o, col.decks.name(did), col.decks.name(odid or did))  # type: ignore[arg-type]
        for cid, o, did, odid in rows
    ]
    return HeldNote(nid, model["name"], dict(note.items()), list(note.tags), cards)


def make_backend(name: str, root: Path) -> Backend:
    """A backend with an empty collection under `root`."""
    if name == "fake":
        return FakeBackend()
    base = make_anki_base(root / f"anki_base_{name}")
    return DirectBackend(base) if name == "direct" else AddonBackend(base)


def edit_vault(md: Path, change: Callable[[str], str]) -> None:
    """Edit a vault file as a person would, a while after the last sync wrote it.

    The stat cache treats a file whose size is unchanged and whose mtime is within 1 ms of the
    recorded one as unchanged (`ContentCache.get_file_meta_by_stat`). A test edits faster than
    a person can, so the mtime is moved a second past the previous one.
    """
    before = md.stat().st_mtime_ns
    md.write_text(change(md.read_text(encoding="utf-8")), encoding="utf-8")
    after = max(md.stat().st_mtime_ns, before + 1_000_000_000)
    os.utime(md, ns=(after, after))


# ---------------------------------------------------------------------------
# Running a sync
# ---------------------------------------------------------------------------


def sync_config(root: Path, vault: Path, backend: Backend, **overrides: Any) -> AppConfig:
    """A config entirely inside `root`. One worker, so batches run in one order.

    The vault path is resolved, as the CLI resolves it: prune runs only when the scanned
    root and the vault root are the same path.
    """
    vault = vault.resolve()
    media = root / "media"
    media.mkdir(exist_ok=True)
    anki_base = backend.anki_base if isinstance(backend, DirectBackend) else root
    settings: dict[str, Any] = {
        "workers": 1,
        "prune": False,
        "backend": "direct",
        **overrides,
    }
    return AppConfig(
        root_input=vault,
        vault_root=vault,
        anki_media_dir=media,
        anki_base=anki_base,
        cache_db=str(root / "cache.db"),
        log_dir=root / "logs",
        **settings,
    )


async def run_sync(
    backend: Backend, config: AppConfig, **kwargs: Any
) -> tuple[RunStats, list[UpdateItem]]:
    """One whole sync through `backend`; the stats and every `UpdateItem` the bridge returned."""
    returned: list[UpdateItem] = []

    async def _bridge(_config: AppConfig) -> AnkiBridge:
        bridge = backend.bridge()
        sync_notes = bridge.sync_notes

        async def _recording(items):
            results = await sync_notes(items)
            returned.extend(results)
            return results

        bridge.sync_notes = _recording  # type: ignore[method-assign]
        return bridge

    with patch("arete.composition.orchestrator.get_anki_bridge", _bridge):
        stats = await execute_sync(config, **kwargs)
    return stats, returned


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _card_key(vault: Path, path: Path, idx: int) -> str:
    return unicodedata.normalize("NFC", f"{path.relative_to(vault).as_posix()}#{idx}")


def vault_cards(vault: Path) -> dict[str, dict[str, Any]]:
    """Per card key, the card's `id`, `anki.nid` and `anki.cid` as the vault holds them now."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(vault.rglob("*.md")):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        for idx, card in enumerate(meta.get("cards") or [], start=1):
            if not isinstance(card, dict):
                continue
            anki = card.get("anki") if isinstance(card.get("anki"), dict) else {}
            out[_card_key(vault, path, idx)] = {
                "id": card.get("id"),
                "nid": (anki or {}).get("nid"),
                "cid": (anki or {}).get("cid"),
            }
    return out


class _Labels:
    """Replace ids with what they point at: a card key, a note's card, an Arete id's card."""

    def __init__(self, vault: Path, notes: list[HeldNote]):
        """Index the vault's Arete ids and Anki's notes and cards."""
        self.cards = vault_cards(vault)
        self.by_arete_id = {v["id"]: k for k, v in sorted(self.cards.items()) if v["id"]}
        self.note_key = {n.nid: self.key_of(n) for n in notes}
        self.card_of = {c.cid: (n.nid, c.ord) for n in notes for c in n.cards}

    def key_of(self, note: HeldNote) -> str:
        keys = sorted(self.by_arete_id[t] for t in note.tags if t in self.by_arete_id)
        return keys[0] if keys else "unclaimed"

    def text(self, value: str) -> str:
        for arete_id, key in self.by_arete_id.items():
            value = value.replace(arete_id, f"<id of {key}>")
        return value

    def nid(self, key: str, raw: Any) -> str | None:
        if raw in (None, ""):
            return None
        nid = int(raw)
        if nid not in self.note_key:
            return "a nid Anki does not hold"
        owner = self.note_key[nid]
        return "its note" if owner == key else f"the note of {owner}"

    def cid(self, key: str, raw: Any) -> str | None:
        if raw in (None, ""):
            return None
        cid = int(raw)
        if cid not in self.card_of:
            return "a cid Anki does not hold"
        nid, ord_ = self.card_of[cid]
        owner = self.note_key[nid]
        whose = "its note" if owner == key else f"the note of {owner}"
        return f"card ord {ord_} of {whose}"


def _error(text: str | None) -> str | None:
    return None if text is None else re.sub(r"\d{10,}", "<id>", text)


def snapshot(backend: Backend, vault: Path, runs: list[list[UpdateItem]]) -> dict[str, Any]:
    """The comparable end state of the syncs in `runs` (see the module docstring)."""
    notes = backend.notes()
    labels = _Labels(vault, notes)
    held: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        held.setdefault(labels.note_key[note.nid], []).append(
            {
                "model": note.model,
                "fields": {k: labels.text(v) for k, v in sorted(note.fields.items())},
                "tags": sorted(labels.text(t) for t in note.tags),
                "decks": [c.deck for c in note.cards],
            }
        )
    for versions in held.values():
        versions.sort(key=repr)
    return {
        "notes": dict(sorted(held.items())),
        "vault_ids": {
            key: {"nid": labels.nid(key, v["nid"]), "cid": labels.cid(key, v["cid"])}
            for key, v in sorted(labels.cards.items())
        },
        "runs": [_run(labels, vault, run) for run in runs],
    }


def _run(labels: _Labels, vault: Path, updates: list[UpdateItem]) -> list[dict[str, Any]]:
    rows = []
    for u in updates:
        key = _card_key(vault, u.source_file, u.source_index)
        rows.append(
            {
                "card": key,
                "ok": u.ok,
                "error": _error(u.error),
                "new_nid": labels.nid(key, u.new_nid),
                "new_cid": labels.cid(key, u.new_cid),
            }
        )
    return sorted(rows, key=lambda r: r["card"])
