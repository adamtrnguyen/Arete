"""Vault file validation: check YAML frontmatter for arete compatibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arete.domain.constants import PRIMARY_FIELD_NAMES

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Structured result of validating a single arete file."""

    ok: bool = True
    errors: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(
        default_factory=lambda: {"deck": None, "model": None, "cards_found": 0}
    )


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def humanize_error(msg: str) -> str:
    """Translate technical PyYAML errors into user-friendly advice."""
    if "mapping values are not allowed here" in msg:
        return (
            "Indentation Error: You likely have a nested key (like 'bad_indent') "
            "at the wrong level. Check your spaces."
        )
    if "found character '\\t' that cannot start any token" in msg:
        return "Tab Character Error: YAML does not allow tabs. Please use spaces only."
    if "did not find expected key" in msg:
        return "Syntax Error: You might be missing a key name or colon."
    if "found duplicate key" in msg:
        return f"Duplicate Key Error: {msg}"
    if "scanner error" in msg:
        return f"Syntax Error: {msg}"
    if "expected <block end>, but found '?'" in msg:
        return (
            "Indentation Error: A key (like 'nid:' or 'cid:') is likely aligned "
            "with the card's dash '-'. It must be indented further to belong to that card."
        )
    return msg


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_arete_flags(meta: dict, result: ValidationResult) -> None:
    """Validate arete flag, cards presence, and deck/model requirements."""
    is_explicit_arete = meta.get("arete") is True

    if "cards" not in meta and not is_explicit_arete:
        if "card" in meta and isinstance(meta["card"], list):
            result.ok = False
            result.errors.append(
                {"line": 1, "message": "Found 'card' list but expected 'cards'. Possible typo?"}
            )
    elif is_explicit_arete and "cards" not in meta:
        result.ok = False
        result.errors.append(
            {"line": 1, "message": "File marked 'arete: true' but missing 'cards' list."}
        )
        if "card" in meta:
            result.errors.append(
                {"line": 1, "message": "Found 'card' property. Did you mean 'cards'?"}
            )

    if "deck" in meta or "model" in meta or is_explicit_arete:
        if "deck" not in meta and is_explicit_arete:
            result.ok = False
            result.errors.append(
                {"line": 1, "message": "File marked 'arete: true' but missing 'deck' field."}
            )
        if "cards" not in meta:
            result.ok = False
            result.errors.append(
                {
                    "line": 1,
                    "message": "Missing 'cards' list. "
                    "You defined a deck/model but provided no cards.",
                }
            )


def _check_split_cards(cards: list, result: ValidationResult) -> None:
    """Detect cards accidentally split into separate Front/Back list items."""
    _FRONT_KEYS = ("Front", "front", "Text", "text")
    _BACK_KEYS = ("Back", "back", "Extra", "extra")

    for i in range(len(cards) - 1):
        curr, nxt = cards[i], cards[i + 1]
        if not isinstance(curr, dict) or not isinstance(nxt, dict):
            continue

        has_front = any(k in curr for k in _FRONT_KEYS)
        has_back = any(k in curr for k in _BACK_KEYS)
        next_has_front = any(k in nxt for k in _FRONT_KEYS)
        next_has_back = any(k in nxt for k in _BACK_KEYS)

        if has_front and not has_back and next_has_back and not next_has_front:
            result.ok = False
            result.errors.append(
                {
                    "line": curr.get("__line__", 0),
                    "message": (
                        f"Split Card Error (Item #{i + 1}): "
                        "It looks like 'Front' and 'Back' are separated into two list items. "
                        "Ensure they are under the same dash '-'."
                    ),
                }
            )


def _check_single_card(card: Any, i: int, cards: list, result: ValidationResult) -> None:
    """Validate a single card entry within the cards list."""
    if not isinstance(card, dict):
        result.ok = False
        result.errors.append(
            {
                "line": 1,
                "message": (
                    f"Card #{i + 1} is invalid. Expected a dictionary (key: value), "
                    f"but got {type(card).__name__}."
                ),
            }
        )
        return

    if not {k: v for k, v in card.items() if not k.startswith("__")}:
        result.ok = False
        result.errors.append(
            {"line": card.get("__line__", i + 1), "message": f"Card #{i + 1} is empty."}
        )
        return

    line = card.get("__line__", i + 1)
    keys = {k for k in card.keys() if not k.startswith("__")}
    if keys.intersection(PRIMARY_FIELD_NAMES):
        return

    # Consistency check against first card
    if i > 0 and isinstance(cards[0], dict):
        card0_keys = set(cards[0].keys())
        if "Front" in card0_keys and "Front" not in keys:
            result.ok = False
            result.errors.append(
                {
                    "line": line,
                    "message": f"Card #{i + 1} is missing 'Front' field (present in first card).",
                }
            )
            return
        if "Text" in card0_keys and "Text" not in keys:
            result.ok = False
            result.errors.append(
                {
                    "line": line,
                    "message": f"Card #{i + 1} is missing 'Text' field (present in first card).",
                }
            )
            return

    if len(keys) == 1 and "Back" in keys:
        result.ok = False
        result.errors.append(
            {"line": line, "message": f"Card #{i + 1} has only 'Back' field. Missing 'Front'?"}
        )


def _validate_cards_list(meta: dict, result: ValidationResult) -> None:
    """Validate the 'cards' field: type, structure, and individual cards."""
    is_explicit_arete = meta.get("arete") is True

    if "cards" not in meta:
        return

    if not isinstance(meta["cards"], list):
        result.ok = False
        result.errors.append(
            {
                "line": 1,
                "message": (
                    f"Invalid format for 'cards'. Expected a list (starting with '-'), "
                    f"but got {type(meta['cards']).__name__}."
                ),
            }
        )
        return

    cards = meta["cards"]
    result.stats["cards_found"] = len(cards)

    if not cards and is_explicit_arete:
        result.ok = False
        result.errors.append(
            {"line": 1, "message": "File marked 'arete: true' but 'cards' list is empty."}
        )

    # Stats collection
    result.stats["deck"] = meta.get("deck")
    result.stats["model"] = meta.get("model")

    if len(cards) > 1:
        _check_split_cards(cards, result)

    # Check for missing deck if notes are present
    if is_explicit_arete or len(cards) > 0:
        if not meta.get("deck"):
            result.ok = False
            result.errors.append(
                {
                    "line": meta.get("__line__", 1),
                    "message": "Missing required field: 'deck'. "
                    "Arete notes must specify a destination deck.",
                }
            )

    for i, card in enumerate(cards):
        _check_single_card(card, i, cards, result)


# ---------------------------------------------------------------------------
# Top-level validation entry point
# ---------------------------------------------------------------------------


def validate_arete_file(path: Path) -> ValidationResult:
    """Validate a single file for arete compatibility.

    Reads the file, parses YAML frontmatter, and runs all validation checks.
    Returns a structured ``ValidationResult``.
    """
    from yaml import YAMLError

    from arete.application.utils.text import validate_frontmatter

    result = ValidationResult()

    if not path.exists():
        result.ok = False
        result.errors.append({"line": 0, "message": "File not found."})
        return result

    content = path.read_text(encoding="utf-8")

    try:
        meta = validate_frontmatter(content)
    except YAMLError as e_raw:
        e: Any = e_raw
        result.ok = False
        line = e.problem_mark.line + 1 if hasattr(e, "problem_mark") else 1  # type: ignore
        col = e.problem_mark.column + 1 if hasattr(e, "problem_mark") else 1  # type: ignore
        tech_msg = f"{e.problem}"  # type: ignore
        if hasattr(e, "context") and e.context:
            tech_msg += f" ({e.context})"
        result.errors.append(
            {
                "line": line,
                "column": col,
                "message": humanize_error(tech_msg),
                "technical": tech_msg,
            }
        )
    except Exception as e:
        result.ok = False
        result.errors.append({"line": 1, "message": str(e)})
    else:
        _check_arete_flags(meta, result)
        _validate_cards_list(meta, result)
        result.stats["deck"] = meta.get("deck")
        result.stats["model"] = meta.get("model")

    return result
