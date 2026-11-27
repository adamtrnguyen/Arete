import pytest
import respx
from httpx import Response

from arete.infrastructure.adapters.anki_connect import AnkiConnectAdapter


@pytest.fixture
def adapter():
    return AnkiConnectAdapter(url="http://mock-anki:8765")


@pytest.mark.asyncio
@respx.mock
async def test_prune_methods(adapter):
    def side_effect(request):
        import json

        data = json.loads(request.content)
        action = data["action"]
        if action == "deckNames":
            return Response(200, json={"result": ["Default", "Math"], "error": None})
        elif action == "findNotes":
            return Response(200, json={"result": [10, 11], "error": None})
        elif action == "notesInfo":
            return Response(
                200,
                json={
                    "result": [
                        {"noteId": 10, "fields": {"nid": {"value": "obs-1"}}},
                        {"noteId": 11, "fields": {}},
                    ],
                    "error": None,
                },
            )
        elif action in ("deleteNotes", "deleteDecks"):
            return Response(200, json={"result": None, "error": None})

        return Response(200, json={"result": None, "error": f"Unexpected {action}"})

    respx.post("http://mock-anki:8765").mock(side_effect=side_effect)

    # 1. deckNames
    decks = await adapter.get_deck_names()
    assert "Math" in decks

    # 2. get_notes_in_deck
    preview = await adapter.get_notes_in_deck("Math")
    assert preview == {"10": 10, "11": 11}

    # 3. delete_notes
    assert await adapter.delete_notes([10, 11])

    # 4. delete_decks
    assert await adapter.delete_decks(["Chemistry"])


@pytest.mark.asyncio
@respx.mock
async def test_get_filtered_deck_names_identifies_dynamic_decks_and_raises_on_error(adapter):
    import json

    def ok_side_effect(request):
        data = json.loads(request.content)
        if data["action"] == "deckNames":
            return Response(
                200, json={"result": ["Default", "Math", "Arete::Queue"], "error": None}
            )
        if data["action"] == "multi":
            replies = []
            for sub in data["params"]["actions"]:
                deck = sub["params"]["deck"]
                cfg = {"dyn": 1} if deck == "Arete::Queue" else {"dyn": 0, "id": 1}
                replies.append({"result": cfg, "error": None})
            return Response(200, json={"result": replies, "error": None})
        return Response(200, json={"result": None, "error": f"Unexpected {data['action']}"})

    respx.post("http://mock-anki:8765").mock(side_effect=ok_side_effect)
    assert await adapter.get_filtered_deck_names() == {"Arete::Queue"}

    def fail_side_effect(request):
        data = json.loads(request.content)
        if data["action"] == "deckNames":
            return Response(
                200, json={"result": ["Default", "Math", "Arete::Queue"], "error": None}
            )
        if data["action"] == "multi":
            return Response(
                200,
                json={
                    "result": [
                        {"result": {"dyn": 0}, "error": None},
                        {"result": None, "error": "collection unavailable"},
                    ],
                    "error": None,
                },
            )
        return Response(200, json={"result": None, "error": f"Unexpected {data['action']}"})

    respx.post("http://mock-anki:8765").mock(side_effect=fail_side_effect)
    with pytest.raises(RuntimeError, match="Arete::Queue"):
        await adapter.get_filtered_deck_names()
