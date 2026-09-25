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
