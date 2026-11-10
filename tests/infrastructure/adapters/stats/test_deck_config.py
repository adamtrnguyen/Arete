"""Deck-config FSRS params, read from the JSON a real collection stores."""

from pathlib import Path

import pytest
from anki.collection import Collection

from arete.infrastructure.adapters.stats.deck_config import deck_params, fsrs_weights

W6 = [0.2, 1.2, 3.3, 16.0, 7.1, 0.5, 1.3, 0.04, 1.5, 0.15, 1.0, 1.9, 0.11, 0.29, 2.3, 0.23]
W6 += [3.0, 0.5, 0.3, 0.1, 0.2]


@pytest.fixture
def col(tmp_path: Path):
    c = Collection(str(tmp_path / "collection.anki2"))
    yield c
    c.close()


def _stored(col: Collection, **changes) -> dict:
    """The deck's config after writing `changes` and reading it back, as both backends see it."""
    did = col.decks.id("Deck")
    conf = col.decks.config_dict_for_deck_id(did)
    conf.update(changes)
    col.decks.update_config(conf)
    stored = col.decks.get_config(conf["id"])
    assert stored is not None
    return stored


def test_a_collection_stores_no_fsrs_sub_dict(col):
    stored = _stored(col)

    assert "fsrs" not in stored
    assert {"desiredRetention", "fsrsParams6", "fsrsParams5", "fsrsWeights"} <= set(stored)


def test_the_weights_are_the_ones_anki_saved(col):
    stored = _stored(col, desiredRetention=0.85, fsrsParams6=W6)

    params = deck_params(stored)

    assert params["desired_retention"] == pytest.approx(0.85)
    assert params["weights"] == pytest.approx(W6)


def test_a_deck_with_no_params_has_no_weights(col):
    assert deck_params(_stored(col))["weights"] == []


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"fsrsParams6": [6.0], "fsrsParams5": [5.0], "fsrsWeights": [4.0]}, [6.0]),
        ({"fsrsParams6": [], "fsrsParams5": [5.0], "fsrsWeights": [4.0]}, [5.0]),
        ({"fsrsParams6": [], "fsrsParams5": [], "fsrsWeights": [4.0]}, [4.0]),
    ],
    ids=["fsrs6", "fsrs5", "fsrs4"],
)
def test_the_weights_are_the_first_set_anki_would_schedule_with(config, expected):
    assert fsrs_weights(config) == expected
