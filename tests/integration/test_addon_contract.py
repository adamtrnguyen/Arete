"""The add-on's wire contract: action dispatch, reply shapes and error texts.

Two clients the repo does not own call these actions, so a reply that changes shape or
wording breaks them silently. Each test pins one reply exactly, against the container.
"""

import pytest
import requests

DECK = "AddonContract"
MISSING = 1234567890123


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


def test_notes_info_batch_equals_one_note_at_a_time(anki_url, notes):
    nids = [nid for nid, _ in notes]
    asked = [nids[2], MISSING, nids[0], nids[2], nids[1]]
    batch = call(anki_url, "notesInfo", notes=asked)["result"]
    singles = [call(anki_url, "notesInfo", notes=[nid])["result"][0] for nid in asked]
    assert batch == singles
    assert batch[1] == {}
    assert [n["cards"] for n in batch if n] == [[notes[i][1]] for i in (2, 0, 2, 1)]


def test_cards_info_batch_equals_one_card_at_a_time(anki_url, notes):
    cids = [cid for _, cid in notes]
    asked = [cids[1], MISSING, cids[0], cids[1]]
    batch = call(anki_url, "cardsInfo", cards=asked)["result"]
    singles = [call(anki_url, "cardsInfo", cards=[cid])["result"][0] for cid in asked]
    assert batch == singles
    assert batch[1] == {}
    assert batch[0]["deckName"] == DECK


def test_reviews_of_cards_keep_request_order_and_per_card_rows(anki_url, notes):
    cids = [cid for _, cid in notes]
    answers = [{"cardId": cids[0], "ease": 3}, {"cardId": cids[2], "ease": 3}]
    call(anki_url, "answerCards", answers=answers)
    call(anki_url, "answerCards", answers=[{"cardId": cids[0], "ease": 2}])
    asked = [cids[2], MISSING, cids[0], cids[1], cids[2]]
    batch = call(anki_url, "getReviewsOfCards", cards=asked)["result"]
    assert list(batch) == [str(cids[2]), str(MISSING), str(cids[0]), str(cids[1])]
    for cid in (cids[0], cids[1], cids[2]):
        single = call(anki_url, "getReviewsOfCards", cards=[cid])["result"]
        assert batch[str(cid)] == single[str(cid)]
    assert [r["ease"] for r in batch[str(cids[0])]] == [3, 2]
    assert batch[str(MISSING)] == []
    assert batch[str(cids[1])] == []


def test_suspend_reply_and_effect(anki_url, notes):
    """Pins the present reply of suspend and unsuspend, quirk included.

    suspend removes already-suspended cards from the list it is iterating, which skips
    the next element: two suspended cards in a row still reply True.
    """
    a, b, c = (cid for _, cid in notes)
    assert call(anki_url, "suspend", cards=[a, b]) == {"result": True, "error": None}
    assert call(anki_url, "suspend", cards=[a, b]) == {"result": True, "error": None}
    assert call(anki_url, "suspend", cards=[a]) == {"result": False, "error": None}
    assert call(anki_url, "areSuspended", cards=[a, b, c])["result"] == [True, True, False]
    assert call(anki_url, "unsuspend", cards=[a, c]) == {"result": None, "error": None}
    assert call(anki_url, "areSuspended", cards=[a, b, c])["result"] == [False, True, False]
    assert call(anki_url, "suspend", cards=[MISSING]) == {
        "result": None,
        "error": f"Card was not found: {MISSING}",
    }
    call(anki_url, "unsuspend", cards=[b])


def test_filtered_deck_orders_cards_by_request_position(anki_url, notes):
    a, b, c = (cid for _, cid in notes)
    name = f"{DECK}::Queue"
    did = call(anki_url, "createFilteredDeck", name=name, cids=[c, a, MISSING, b, c])["result"]
    assert isinstance(did, int)
    infos = call(anki_url, "cardsInfo", cards=[a, b, c])["result"]
    assert [i["deckName"] for i in infos] == [name] * 3
    # due = 1000 + position in the request; a repeated card takes its last position
    assert [i["due"] for i in infos] == [1001, 1003, 1004]
    call(anki_url, "deleteDecks", decks=[name], cardsToo=True)


def test_every_reply_says_the_connection_closes(anki_url):
    """The add-on drops each connection after one reply, and must say so.

    Without the header an HTTP client pools the connection and later sends a request
    into it, which fails with "Server disconnected without sending a response".
    """
    ok = requests.post(anki_url, json={"action": "version", "version": 6})
    empty = requests.post(anki_url, data=b"")
    assert ok.headers.get("Connection") == "close"
    assert empty.headers.get("Connection") == "close"
