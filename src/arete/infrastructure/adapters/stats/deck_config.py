"""FSRS parameters from a deck config as Anki stores it.

Both backends read the same JSON: `col.decks.get_config` directly, and the add-on's
`getDeckConfig`, which returns `col.decks.config_dict_for_deck_id`. Its keys are Anki's
`DeckConfSchema11` (rslib/src/deckconfig/schema11.rs, serde camelCase): `desiredRetention`
and `sm2Retention` at the top level, and the FSRS parameters in `fsrsParams6`,
`fsrsParams5` and `fsrsWeights` (the FSRS-4 set).
"""

from __future__ import annotations

from typing import Any

DEFAULT_RETENTION = 0.9


def default_params() -> dict[str, Any]:
    """The params of a deck whose config could not be read."""
    return {"desired_retention": DEFAULT_RETENTION, "weights": [], "sm2_retention": 0.9}


def fsrs_weights(config: dict[str, Any]) -> list[float]:
    """The FSRS parameters Anki schedules the deck with.

    The first non-empty of `fsrsParams6`, `fsrsParams5`, `fsrsWeights`, as Anki's
    `DeckConfig::fsrs_params` picks them (rslib/src/deckconfig/mod.rs).
    """
    for key in ("fsrsParams6", "fsrsParams5", "fsrsWeights"):
        if weights := config.get(key):
            return list(weights)
    return []


def deck_params(config: dict[str, Any]) -> dict[str, Any]:
    """Desired retention, FSRS weights and historical retention of one deck config."""
    return {
        "desired_retention": config.get("desiredRetention", DEFAULT_RETENTION),
        "weights": fsrs_weights(config),
        "sm2_retention": config.get("sm2Retention", 0.9),
    }
