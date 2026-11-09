"""The add-on's wire contract: action dispatch, reply shapes and error texts.

Two clients the repo does not own call these actions, so a reply that changes shape or
wording breaks them silently. Each test pins one reply exactly, against the container.
"""

import pytest
import requests

DECK = "AddonContract"


def call(url, action, version=6, **params):
    resp = requests.post(url, json={"action": action, "version": version, "params": params})
    return resp.json()


@pytest.fixture
def notes(anki_url):
    """Three fresh Basic notes in their own deck: [(nid, cid), ...]."""
    call(anki_url, "createDeck", deck=DECK)
    old = call(anki_url, "findNotes", query=f"deck:{DECK}")["result"]
    if old:
        call(anki_url, "deleteNotes", notes=old)
    out = []
    for i in range(3):
        nid = call(
            anki_url,
            "addNote",
            note={
                "deckName": DECK,
                "modelName": "Basic",
                "fields": {"Front": f"contract {i}", "Back": f"back {i}"},
                "tags": [f"contract_{i}"],
            },
        )["result"]
        cid = call(anki_url, "findCards", query=f"nid:{nid}")["result"][0]
        out.append((nid, cid))
    return out


def test_unknown_action_is_refused(anki_url):
    assert call(anki_url, "noSuchAction") == {"result": None, "error": "unsupported action"}


def test_helper_method_is_not_an_action(anki_url):
    assert call(anki_url, "getModel", modelName="Basic") == {
        "result": None,
        "error": "unsupported action",
    }


def test_version_4_reply_is_the_bare_result(anki_url):
    names = call(anki_url, "deckNames", version=4)
    assert isinstance(names, list)
    assert "Default" in names


def test_multi_dispatches_each_sub_action(anki_url):
    unsupported = {"result": None, "error": "unsupported action"}
    reply = call(
        anki_url,
        "multi",
        actions=[
            {"action": "version"},
            {"action": "version", "version": 6},
            {"action": "noSuchAction"},
            {"action": ["unhashable"]},
            {"action": 5},
            {},
            {"action": "multi", "params": {"actions": [{"action": "version"}]}},
            {"action": "version", "key": "wrong"},
        ],
    )
    assert reply == {
        "result": [
            6,
            {"result": 6, "error": None},
            unsupported,
            unsupported,
            unsupported,
            unsupported,
            [6],
            {"result": None, "error": "valid api key must be provided"},
        ],
        "error": None,
    }
