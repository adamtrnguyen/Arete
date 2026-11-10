"""The bundled add-on's action lookup: one table, by method name, for every request version."""

import asyncio
import inspect
from pathlib import Path

import pytest

from tests.e2e.addon_host import AddonHost


@pytest.fixture
def host(tmp_path: Path):
    h = AddonHost(tmp_path / "collection.anki2")
    yield h
    h.close()


def test_the_action_table_holds_every_api_method(host: AddonHost):
    addon = host._addon
    api_methods = {
        name
        for name, method in inspect.getmembers(addon, predicate=inspect.ismethod)
        if getattr(method, "api", False)
    }
    assert addon.actions is not None
    assert set(addon.actions) == api_methods
    assert {"deckNames", "multi", "createFilteredDeck", "setHomeDeck"} <= api_methods


@pytest.mark.parametrize(
    ("version", "reply"),
    [
        (4, ["Default"]),
        (6, {"result": ["Default"], "error": None}),
    ],
)
def test_a_request_reaches_the_same_action_at_any_version(
    host: AddonHost, version: int, reply: object
):
    assert asyncio.run(host.http({"action": "deckNames", "version": version})) == reply


def test_an_unknown_action_is_unsupported(host: AddonHost):
    assert asyncio.run(host.http({"action": "noSuchAction", "version": 6})) == {
        "result": None,
        "error": "unsupported action",
    }
