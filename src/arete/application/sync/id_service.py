"""Service for managing stable Arete IDs for cards."""

import logging
from typing import Any

from ulid import ULID

logger = logging.getLogger(__name__)


def generate_arete_id() -> str:
    """Generate a stable Arete ID using ULID."""
    return f"arete_{ULID()}"


def ensure_card_ids(meta: dict[str, Any]) -> int:
    """Ensure all cards in the metadata have an ID.

    Modifies the metadata in-place.
    Returns the number of IDs assigned.
    """
    if not meta or "cards" not in meta:
        return 0

    cards = meta.get("cards", [])
    if not isinstance(cards, list):
        return 0

    ids_assigned = 0
    for card in cards:
        if not isinstance(card, dict):
            continue

        if "id" not in card:
            card["id"] = generate_arete_id()
            ids_assigned += 1

    return ids_assigned
