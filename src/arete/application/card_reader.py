"""Application service for reading card data from vault markdown files.

Pure file-based operations — no Anki connection needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arete.application.utils.text import normalize_filename, parse_frontmatter

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConceptCardsResult:
    """Result of reading cards for a concept from the vault."""

    concept: str
    file: str
    card_count: int
    cards: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FileCardsResult:
    """Result of reading all cards from a specific file."""

    file: str
    basename: str
    deck: str
    tags: list[str]
    card_count: int
    cards: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_concept_file(vault_root: Path, concept: str) -> Path | None:
    """Find a concept note in the vault by name.

    Tries exact match first, then case-insensitive search (root + one level deep).
    """
    # Exact match (Path comparison handles NFC↔NFD on macOS, but we normalize
    # the concept input so `vault_root / "Cramér.md"` finds the file regardless
    # of which form the user typed).
    exact = vault_root / f"{normalize_filename(concept)}.md"
    if exact.exists():
        return exact

    # Case-insensitive search in vault root. Normalize both sides to NFC before
    # lowercasing so accented filenames match regardless of the form macOS
    # returns from pathlib.
    concept_key = normalize_filename(concept).lower()
    for p in vault_root.iterdir():
        if p.suffix == ".md" and normalize_filename(p.stem).lower() == concept_key:
            return p

    # Search subdirectories (one level deep)
    for d in vault_root.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            for p in d.iterdir():
                if p.suffix == ".md" and normalize_filename(p.stem).lower() == concept_key:
                    return p

    return None


def _extract_card_entry(card: dict[str, Any], index: int, card_deck: str) -> dict[str, Any]:
    """Extract a card's display fields into a summary dict."""
    entry: dict[str, Any] = {"index": index}
    if card.get("id"):
        entry["arete_id"] = card["id"]
    if card.get("model"):
        entry["model"] = card["model"]
    entry["deck"] = card_deck

    # Content fields
    for key in ("Front", "Back", "Text", "Back Extra"):
        value = card.get(key)
        if value:
            entry[key] = value

    deps = card.get("deps", {})
    if deps:
        entry["deps"] = deps
    return entry


def get_concept_cards(
    vault_root: Path, concept: str, deck_filter: str = ""
) -> ConceptCardsResult | str:
    """Read cards for a concept from the vault.

    Returns a ``ConceptCardsResult`` on success, or an error string on failure.
    """
    concept_path = find_concept_file(vault_root, concept)
    if concept_path is None:
        return f"No vault note found for concept '{concept}'"

    text = concept_path.read_text(encoding="utf-8", errors="replace")
    meta, _ = parse_frontmatter(text)
    if not meta or "__yaml_error__" in meta:
        return f"Error parsing frontmatter in {concept_path.name}"
    if meta.get("arete") is not True:
        return f"{concept_path.name} is not an Arete note (missing arete: true)"

    cards = meta.get("cards", [])
    if not cards:
        return f"No cards found in {concept_path.name}"

    doc_deck = meta.get("deck", "")
    results: list[dict[str, Any]] = []
    for i, card in enumerate(cards, 1):
        if not isinstance(card, dict):
            continue
        card_deck = card.get("deck", doc_deck)
        if deck_filter and card_deck and deck_filter not in card_deck:
            continue
        results.append(_extract_card_entry(card, i, card_deck))

    if not results:
        suffix = f" (deck filter: {deck_filter})" if deck_filter else ""
        return f"No cards matched in {concept_path.name}" + suffix

    return ConceptCardsResult(
        concept=concept,
        file=concept_path.name,
        card_count=len(results),
        cards=results,
    )


def list_file_cards(file_path: Path) -> FileCardsResult | str:
    """Extract all Arete cards from a markdown file.

    Returns a ``FileCardsResult`` on success, or an error string on failure.
    """
    if not file_path.exists():
        return f"File not found: {file_path}"

    text = file_path.read_text(encoding="utf-8", errors="replace")
    meta, _ = parse_frontmatter(text)
    if not meta or "__yaml_error__" in meta:
        return f"Failed to parse frontmatter in {file_path.name}"

    if meta.get("arete") is not True:
        return f"{file_path.name} is not an Arete note (missing arete: true)"

    file_deck = meta.get("deck", "")
    file_tags = meta.get("tags", [])
    file_model = meta.get("model", "Basic")
    cards_raw = meta.get("cards", [])

    cards_out: list[dict[str, Any]] = []
    for i, card in enumerate(cards_raw):
        if not isinstance(card, dict):
            continue

        entry: dict[str, Any] = {
            "index": i,
            "id": card.get("id"),
            "model": card.get("model", file_model),
            "deck": card.get("deck", file_deck),
            "tags": card.get("tags", file_tags),
        }

        # Content fields
        for key in ("Front", "Back", "Text", "Back Extra"):
            value = card.get(key)
            if value:
                entry[key] = value

        # Deps — always include, even if empty
        deps = card.get("deps", {})
        entry["deps"] = {
            "requires": deps.get("requires", []) if isinstance(deps, dict) else [],
            "related": deps.get("related", []) if isinstance(deps, dict) else [],
        }

        cards_out.append(entry)

    return FileCardsResult(
        file=str(file_path),
        basename=file_path.stem,
        deck=file_deck,
        tags=file_tags,
        card_count=len(cards_out),
        cards=cards_out,
    )


def get_note_body(file_path: Path) -> str:
    """Read a markdown file and return its body, stripping YAML frontmatter.

    Returns the body text, or an error string prefixed with "Error:" on failure.
    """
    if not file_path.exists():
        return f"Error: File not found: {file_path}"

    text = file_path.read_text(encoding="utf-8", errors="replace")
    _, body = parse_frontmatter(text)
    return body.strip() if body else "(empty body)"
