"""Card editing service with maturity-based stability guards.

Provides controlled card-editing operations that check Anki maturity
before allowing changes, preventing knowledge perturbance on mature cards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from arete.application.sync.id_service import generate_arete_id
from arete.application.utils.text import parse_frontmatter, rebuild_markdown_with_frontmatter
from arete.domain.card_models import parse_card
from arete.domain.interfaces import AnkiBridge

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Maturity classification
# ---------------------------------------------------------------------------

MATURE_THRESHOLD = 21  # days
CONTENT_FIELDS = {"Front", "Back", "Back Extra", "Text"}
DEP_FIELDS = {"deps"}
ALLOWED_FIELDS = CONTENT_FIELDS | DEP_FIELDS | {"tags"}


def classify_maturity(interval: int) -> str:
    """Classify a card's maturity based on its Anki interval.

    Returns "mature", "young", or "new".
    """
    if interval > MATURE_THRESHOLD:
        return "mature"
    elif interval >= 1:
        return "young"
    return "new"


def check_edit_policy(maturity: str, field_name: str) -> str:
    """Check whether editing a field is allowed given the card's maturity.

    Returns "allowed", "warned", or "blocked".
    """
    if field_name in DEP_FIELDS:
        return "allowed"

    if field_name == "Front":
        if maturity == "mature":
            return "warned"
        elif maturity == "young":
            return "warned"
        return "allowed"

    if field_name in ("Back", "Back Extra", "Text"):
        if maturity == "mature":
            return "warned"
        return "allowed"

    if field_name == "tags":
        return "allowed"

    return "allowed"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class EditResult:
    success: bool
    message: str
    applied: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    maturity: str = "unknown"


@dataclass
class AddResult:
    success: bool
    message: str
    index: int = -1
    arete_id: str = ""


@dataclass
class DeleteResult:
    success: bool
    message: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_and_parse(file_path: Path) -> tuple[dict[str, Any], str, str]:
    """Read a file and parse frontmatter. Returns (meta, body, raw_text)."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    raw = file_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw)

    if not meta or "__yaml_error__" in meta:
        raise ValueError(f"Failed to parse frontmatter in {file_path.name}")

    return meta, body, raw


def _get_card_nid(card: dict[str, Any]) -> int | None:
    """Extract the Anki NID from a card dict. Returns None if not synced."""
    anki_block = card.get("anki", {})
    if isinstance(anki_block, dict):
        nid = anki_block.get("nid")
        if nid:
            return int(str(nid).strip("'\""))
    return None


async def _get_maturity(bridge: AnkiBridge | None, card: dict[str, Any]) -> str:
    """Get maturity classification for a card. Defaults to mature if offline."""
    nid = _get_card_nid(card)
    if nid is None:
        return "new"

    if bridge is None:
        return "mature"  # Safe default when Anki unavailable

    try:
        stats = await bridge.get_card_stats([nid])
        if not stats:
            return "new"  # Card exists in YAML but not in Anki
        # Use max interval across all card variants (front/back for cloze)
        max_interval = max(s.interval for s in stats)
        return classify_maturity(max_interval)
    except Exception:
        logger.warning(f"Failed to fetch stats for NID {nid}, defaulting to mature")
        return "mature"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def edit_body(file_path: Path, old_text: str, new_text: str) -> EditResult:
    """Edit the markdown body (below frontmatter). No maturity restrictions.

    If old_text is empty and the body is empty/whitespace, sets the body to new_text.
    """
    meta, body, _ = _read_and_parse(file_path)

    # Special case: setting body on an empty/scaffold note
    if old_text == "" and body.strip() == "":
        new_body = new_text
    elif old_text not in body:
        # Check if it's in frontmatter instead
        raw = file_path.read_text(encoding="utf-8")
        fm_end = raw.find("\n---\n", raw.find("---") + 3)
        if fm_end >= 0 and old_text in raw[:fm_end]:
            return EditResult(
                success=False,
                message=(
                    "old_text was found in frontmatter, not body."
                    " Use edit_card for frontmatter changes."
                ),
            )
        return EditResult(
            success=False,
            message="old_text not found in note body.",
        )
    else:
        new_body = body.replace(old_text, new_text, 1)

    result = rebuild_markdown_with_frontmatter(meta, new_body)
    file_path.write_text(result, encoding="utf-8")

    return EditResult(
        success=True,
        message="Body edited successfully.",
        applied=["body"],
    )


async def edit_card(
    file_path: Path,
    card_index: int,
    fields: dict[str, Any],
    bridge: AnkiBridge | None = None,
    force: bool = False,
) -> EditResult:
    """Edit fields of an existing card. Maturity-guarded."""
    meta, body, _ = _read_and_parse(file_path)

    cards = meta.get("cards", [])
    if not isinstance(cards, list) or card_index < 0 or card_index >= len(cards):
        return EditResult(
            success=False,
            message=(
                f"Invalid card_index {card_index}."
                f" File has {len(cards) if isinstance(cards, list) else 0} cards."
            ),
        )

    card = cards[card_index]
    if not isinstance(card, dict):
        return EditResult(success=False, message=f"Card at index {card_index} is not a dict.")

    # Validate field names
    invalid = set(fields.keys()) - ALLOWED_FIELDS
    if invalid:
        return EditResult(
            success=False,
            message=f"Invalid field names: {invalid}. Allowed: {sorted(ALLOWED_FIELDS)}",
        )

    maturity = await _get_maturity(bridge, card)

    applied: list[str] = []
    warned: list[str] = []
    blocked: list[str] = []

    for field_name, value in fields.items():
        policy = check_edit_policy(maturity, field_name)

        if policy == "blocked" and not force:
            blocked.append(field_name)
            continue

        if policy == "warned":
            warned.append(field_name)

        # Apply the edit
        if field_name == "deps":
            card["deps"] = value
        else:
            card[field_name] = value
        applied.append(field_name)

    if not applied and blocked:
        return EditResult(
            success=False,
            message=(
                f"All edits blocked by maturity guard (card is {maturity},"
                " interval >21d). Use force=True to override."
            ),
            blocked=blocked,
            maturity=maturity,
        )

    if applied:
        # Validate the card after edits
        default_model = meta.get("model", "Basic")
        try:
            parse_card(card, default_model=default_model)
        except ValidationError as exc:
            errors = "; ".join(e["msg"] for e in exc.errors())
            return EditResult(
                success=False,
                message=f"Edit would produce invalid card: {errors}",
                maturity=maturity,
            )

        result = rebuild_markdown_with_frontmatter(meta, body)
        file_path.write_text(result, encoding="utf-8")

    return EditResult(
        success=True,
        message="Card edited." + (f" Blocked: {blocked}" if blocked else ""),
        applied=applied,
        warned=warned,
        blocked=blocked,
        maturity=maturity,
    )


async def add_card(file_path: Path, card_dict: dict[str, Any]) -> AddResult:
    """Append a new card. No maturity check (new cards are always new)."""
    meta, body, _ = _read_and_parse(file_path)

    if "cards" not in meta:
        meta["cards"] = []
    cards = meta["cards"]
    if not isinstance(cards, list):
        return AddResult(success=False, message="Existing 'cards' field is not a list.")

    # Validate card structure before writing
    default_model = meta.get("model", "Basic")
    try:
        parse_card(card_dict, default_model=default_model)
    except ValidationError as exc:
        errors = "; ".join(e["msg"] for e in exc.errors())
        return AddResult(success=False, message=f"Invalid card: {errors}")

    # Auto-assign ID if not provided
    if "id" not in card_dict:
        card_dict["id"] = generate_arete_id()

    # Ensure deps structure
    if "deps" not in card_dict:
        card_dict["deps"] = {"requires": [], "related": []}

    cards.append(card_dict)
    index = len(cards) - 1

    result = rebuild_markdown_with_frontmatter(meta, body)
    file_path.write_text(result, encoding="utf-8")

    return AddResult(
        success=True,
        message=f"Card added at index {index}.",
        index=index,
        arete_id=card_dict["id"],
    )


async def delete_card(
    file_path: Path,
    card_index: int,
    bridge: AnkiBridge | None = None,
    force: bool = False,
) -> DeleteResult:
    """Remove a card by index. Maturity-guarded."""
    meta, body, _ = _read_and_parse(file_path)

    cards = meta.get("cards", [])
    if not isinstance(cards, list) or card_index < 0 or card_index >= len(cards):
        return DeleteResult(
            success=False,
            message=(
                f"Invalid card_index {card_index}."
                f" File has {len(cards) if isinstance(cards, list) else 0} cards."
            ),
        )

    card = cards[card_index]
    if not isinstance(card, dict):
        return DeleteResult(success=False, message=f"Card at index {card_index} is not a dict.")

    maturity = await _get_maturity(bridge, card)

    if maturity == "mature" and not force:
        front = card.get("Front", card.get("Text", "(unknown)"))
        return DeleteResult(
            success=False,
            message=(
                f"Cannot delete mature card (interval >21d)."
                f" Front: '{front}'. Use force=True to override."
            ),
        )

    if maturity == "young" and not force:
        front = card.get("Front", card.get("Text", "(unknown)"))
        return DeleteResult(
            success=False,
            message=(
                f"Warning: deleting young card (1-21d interval)."
                f" Front: '{front}'. Use force=True to override."
            ),
        )

    cards.pop(card_index)
    result = rebuild_markdown_with_frontmatter(meta, body)
    file_path.write_text(result, encoding="utf-8")

    return DeleteResult(
        success=True,
        message=f"Card at index {card_index} deleted.",
    )
