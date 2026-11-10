"""The bundled add-on calls no Anki API that prints a deprecation warning.

Anki 26.8 keeps its pre-2.1.45 camelCase names and several `@deprecated` wrappers alive. Each
call to one prints a line (`<path>:<line>:<name> is deprecated: please use '<new>'`) with a
stack walk, to stdout, through `anki._legacy.print_deprecation_warning`. Two checks pin that
the add-on makes none:

1. Driven: each action below runs through the add-on's own handler over a real collection, with
   that function recording every call. A deprecated call on the action's path fails the test.
2. Static: every attribute the add-on source reads is looked up on the Anki classes it can be
   called on; a name that Anki forwards through its legacy shim, or wraps in `@deprecated`,
   fails. This covers the actions that need Anki's GUI and cannot be driven here.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anki._legacy
import anki.utils
import pytest
from anki.cards import Card
from anki.collection import Collection
from anki.db import DB
from anki.decks import DeckManager
from anki.httpclient import HttpClient
from anki.media import MediaManager
from anki.models import ModelManager
from anki.notes import Note
from anki.scheduler.v3 import Scheduler
from anki.tags import TagManager

from tests.e2e.addon_host import ADDON_DIR, AddonHost

ANKI_CLASSES = (
    Collection,
    DeckManager,
    ModelManager,
    TagManager,
    MediaManager,
    Note,
    Card,
    Scheduler,
    DB,
    HttpClient,
)

# (attribute, receiver as written) pairs the static check lets through, each with its reason.
ALLOWED: dict[tuple[str, str], str] = {
    ("getConfig", "manager"): "aqt's AddonManager.getConfig, not Collection.get_config",
    ("getConfig", "aqt.mw.addonManager"): "aqt's AddonManager.getConfig, not Collection.get_config",
    ("reset", "self.window()"): "aqt's AnkiQt.reset redraws the GUI; Collection.reset is the no-op",
    ("save", "collection.decks"): "DeckManager.save is not deprecated for a deck or a config",
    ("save", "col.decks"): "DeckManager.save is not deprecated for a deck or a config",
    ("flush", "self.log"): "a file object",
    (
        "stripIllegal",
        "self.media()",
    ): "Anki offers no public replacement (media.py _legacy_strip_illegal)",
}


@pytest.fixture
def deprecations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every message Anki's deprecation machinery prints, instead of printing it."""
    seen: list[str] = []

    def record(msg: str, frame: int = 1) -> None:
        seen.append(msg)

    for module in (anki._legacy, anki.decks, anki.models):
        monkeypatch.setattr(module, "print_deprecation_warning", record)
    return seen


@pytest.fixture
def host(tmp_path: Path) -> Iterator[AddonHost]:
    h = AddonHost(tmp_path / "collection.anki2")
    yield h
    h.close()


def call(host: AddonHost, action: str, **params: Any) -> Any:
    reply = asyncio.run(host.http({"action": action, "version": 6, "params": params}))
    assert reply["error"] is None, f"{action}: {reply['error']}"
    return reply["result"]


def seed(host: AddonHost) -> dict[str, Any]:
    """A deck, a note with two tags and a card, as a person would have in Anki."""
    note = host.col.new_note(host.col.models.by_name("Basic"))
    note["Front"], note["Back"] = "front", "back"
    note.tags = ["alpha"]
    host.col.add_note(note, host.col.decks.id("Default"))
    (cid,) = note.card_ids()
    return {"nid": note.id, "cid": cid}


def _driven_actions(host: AddonHost, tmp_path: Path) -> list[tuple[str, dict[str, Any]]]:
    ids = seed(host)
    cid, nid = ids["cid"], ids["nid"]
    config = call(host, "getDeckConfig", deck="Default")
    cloned = call(host, "cloneDeckConfigId", name="copy")
    return [
        ("modelNames", {}),
        ("modelNamesAndIds", {}),
        ("modelFieldNames", {"modelName": "Basic"}),
        ("modelFieldDescriptions", {"modelName": "Basic"}),
        ("modelFieldsOnTemplates", {"modelName": "Basic"}),
        (
            "createModel",
            {
                "modelName": "Made",
                "inOrderFields": ["A", "B"],
                "cardTemplates": [{"Front": "{{A}}", "Back": "{{B}}"}],
            },
        ),
        (
            "updateModelTemplates",
            {"model": {"name": "Basic", "templates": {"Card 1": {"Front": "{{Front}}"}}}},
        ),
        ("updateModelStyling", {"model": {"name": "Basic", "css": ".card {}"}}),
        (
            "findAndReplaceInModels",
            {"modelName": "Basic", "findText": "zzz", "replaceText": "yyy"},
        ),
        ("modelFieldAdd", {"modelName": "Basic", "fieldName": "Extra", "index": 0}),
        ("modelFieldReposition", {"modelName": "Basic", "fieldName": "Extra", "index": 1}),
        ("modelFieldRename", {"modelName": "Basic", "oldFieldName": "Extra", "newFieldName": "X"}),
        ("modelFieldRemove", {"modelName": "Basic", "fieldName": "X"}),
        ("removeEmptyNotes", {}),
        ("saveDeckConfig", {"config": config}),
        ("cloneDeckConfigId", {"name": "second"}),
        ("removeDeckConfigId", {"configId": cloned}),
        ("exportPackage", {"deck": "Default", "path": str(tmp_path / "out.apkg")}),
        ("getNumCardsReviewedToday", {}),
        ("getNumCardsReviewedByDay", {}),
        ("suspend", {"cards": [cid]}),
        ("unsuspend", {"cards": [cid]}),
        ("setEaseFactors", {"cards": [cid], "easeFactors": [2500]}),
        ("setSpecificValueOfCard", {"card": cid, "keys": ["factor"], "newValues": [2600]}),
        ("clearUnusedTags", {}),
        ("replaceTags", {"notes": [nid], "tag_to_replace": "alpha", "replace_with_tag": "beta"}),
        ("replaceTagsInAllNotes", {"tag_to_replace": "beta", "replace_with_tag": "gamma"}),
        ("storeMediaFile", {"filename": "a.txt", "data": base64.b64encode(b"hi").decode()}),
        ("reloadCollection", {}),
        (
            "addNote",
            {
                "note": {
                    "deckName": "Default",
                    "modelName": "Basic",
                    "fields": {"Front": "new q", "Back": "new a"},
                    "options": {"allowDuplicate": False, "duplicateScope": "collection"},
                }
            },
        ),
    ]


def test_driven_actions_print_no_deprecation_warning(
    host: AddonHost, deprecations: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    called: dict[str, list[str]] = {}
    for action, params in _driven_actions(host, tmp_path):
        before = len(deprecations)
        call(host, action, **params)
        if deprecations[before:]:
            called[action] = deprecations[before:]
    assert called == {}
    assert "deprecated" not in capsys.readouterr().out


def test_the_collection_keeps_its_data_through_the_driven_actions(host: AddonHost, tmp_path: Path):
    """The replacements write what the deprecated calls wrote."""
    ids = seed(host)
    call(host, "replaceTags", notes=[ids["nid"]], tag_to_replace="alpha", replace_with_tag="beta")
    assert host.col.get_note(ids["nid"]).tags == ["beta"]
    call(host, "setEaseFactors", cards=[ids["cid"]], easeFactors=[2700])
    assert host.col.get_card(ids["cid"]).factor == 2700
    call(host, "suspend", cards=[ids["cid"]])
    assert host.col.get_card(ids["cid"]).queue == -1
    call(host, "unsuspend", cards=[ids["cid"]])
    assert host.col.get_card(ids["cid"]).queue == 0
    call(host, "storeMediaFile", filename="a.txt", data=base64.b64encode(b"hi").decode())
    assert (Path(host.col.media.dir()) / "a.txt").read_bytes() == b"hi"
    assert call(host, "modelNames") == [n.name for n in host.col.models.all_names_and_ids()]


def _receiver(node: ast.Attribute) -> str:
    return ast.unparse(node.value)


def _is_deprecated_on(cls: type, name: str) -> bool:
    """True when `cls.name` prints a deprecation warning when it is read or called."""
    static = inspect.getattr_static(cls, name, None)
    if static is not None:
        code = getattr(static, "__code__", None)
        return code is not None and Path(code.co_filename).name == "_legacy.py"
    try:
        remapped, _ = anki._legacy._get_remapped_and_replacement(cls, name)  # type: ignore[arg-type]
    except AttributeError:
        return False
    return remapped in dir(cls) or name in cls._deprecated_attributes  # type: ignore[attr-defined]


def _deprecated_reads() -> Iterator[str]:
    for path in sorted(ADDON_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Attribute) or node.attr[:1].isupper():
                continue
            receiver = _receiver(node)
            if receiver == "self" or (node.attr, receiver) in ALLOWED:
                continue
            if receiver == "anki.utils":
                if node.attr not in vars(anki.utils):
                    yield f"{path.name}:{node.lineno}: anki.utils.{node.attr}"
                continue
            if any(_is_deprecated_on(cls, node.attr) for cls in ANKI_CLASSES):
                yield f"{path.name}:{node.lineno}: {receiver}.{node.attr}"


def test_no_add_on_source_line_reads_a_deprecated_anki_name():
    assert list(_deprecated_reads()) == []


def test_the_static_check_sees_a_deprecated_name():
    """The check is live: names Anki deprecates are recognised, current ones are not."""
    checks: list[tuple[type, str, bool]] = [
        (DeckManager, "byName", True),
        (ModelManager, "useCount", True),
        (ModelManager, "rem", True),
        (Note, "flush", True),
        (Card, "flush", True),
        (TagManager, "registerNotes", True),
        (MediaManager, "writeData", True),
        (Scheduler, "dayCutoff", True),
        (Scheduler, "suspendCards", False),
        (DeckManager, "by_name", False),
        (Collection, "update_note", False),
    ]
    wrong = [
        f"{cls.__name__}.{name}"
        for cls, name, want in checks
        if _is_deprecated_on(cls, name) != want
    ]
    assert wrong == []
