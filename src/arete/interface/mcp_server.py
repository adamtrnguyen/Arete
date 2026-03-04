"""Arete MCP Server.

Exposes Arete sync and learning tools via the Model Context Protocol (MCP),
enabling AI agents (Claude, Gemini, etc.) to interact with Anki flashcards.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from arete.application.config import AppConfig, resolve_config
from arete.application.factory import get_anki_bridge
from arete.domain.interfaces import AnkiBridge
from arete.application.orchestrator import execute_sync

logger = logging.getLogger(__name__)


def create_server() -> FastMCP:  # noqa: C901
    """Create and configure the Arete MCP server with all tools."""
    mcp = FastMCP(name="arete")

    _state: dict[str, Any] = {}

    def _config() -> AppConfig:
        if "config" not in _state:
            _state["config"] = resolve_config()
        return _state["config"]

    async def _bridge() -> AnkiBridge:
        if "bridge" not in _state:
            _state["bridge"] = await get_anki_bridge(_config())
        return _state["bridge"]

    # ------------------------------------------------------------------
    # Existing tools (migrated from raw Server)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def sync_vault(
        vault_path: str = "",
        force: bool = False,
        prune: bool = False,
    ) -> str:
        """Sync Obsidian vault to Anki. Returns sync statistics.

        Args:
            vault_path: Path to vault (optional, uses config default)
            force: Force sync all notes, ignoring cache
            prune: Remove orphaned Anki notes

        """
        overrides: dict[str, Any] = {}
        if vault_path:
            overrides["vault_root"] = vault_path
        if force:
            overrides["force"] = True
            overrides["clear_cache"] = True
        if prune:
            overrides["prune"] = True

        config = resolve_config(cli_overrides=overrides if overrides else None)
        stats = await execute_sync(config)

        return json.dumps(
            {
                "success": stats.total_errors == 0,
                "total_generated": stats.total_generated,
                "total_imported": stats.total_imported,
                "total_errors": stats.total_errors,
            },
            indent=2,
        )

    @mcp.tool()
    async def sync_file(
        file_path: str,
        force: bool = False,
    ) -> str:
        """Sync a specific Markdown file to Anki.

        Args:
            file_path: Path to the Markdown file to sync
            force: Force sync, ignoring cache

        """
        path = Path(file_path)
        if not path.exists():
            return f"Error: File not found: {file_path}"

        overrides: dict[str, Any] = {"root_input": path}
        if force:
            overrides["force"] = True
            overrides["clear_cache"] = True

        config = resolve_config(cli_overrides=overrides)
        stats = await execute_sync(config)

        return json.dumps(
            {
                "success": stats.total_errors == 0,
                "file": str(path),
                "total_imported": stats.total_imported,
                "total_errors": stats.total_errors,
            },
            indent=2,
        )

    @mcp.tool()
    async def get_stats(
        lapse_threshold: int = 3,
    ) -> str:
        """Get learning statistics and identify problematic notes.

        Args:
            lapse_threshold: Threshold for lapsing cards (leeches)

        """
        import dataclasses

        from arete.application.stats.learning_insights_service import LearningInsightsService

        bridge = await _bridge()
        service = LearningInsightsService(bridge)
        insights = await service.get_learning_insights(lapse_threshold=lapse_threshold)
        return json.dumps(dataclasses.asdict(insights), indent=2)

    # ------------------------------------------------------------------
    # New learning-focused tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def browse_concept(concept: str, deck: str = "CS::DSA") -> str:
        """Open the Anki card browser filtered to a concept's cards.

        Opens the Anki GUI browser with a search query targeting cards
        in the specified deck that match the concept name.

        Args:
            concept: The concept to browse (e.g. "Hash Table", "Binary Search")
            deck: Deck to search within (default: "CS::DSA")

        """
        query = f'"deck:{deck}" "{concept}"'
        bridge = await _bridge()
        ok = await bridge.gui_browse(query)
        if ok:
            return f"Opened Anki browser for concept '{concept}' in deck {deck}"
        return "Failed to open Anki browser. Is Anki running with AnkiConnect?"

    @mcp.tool()
    async def browse_card(arete_id: str) -> str:
        """Open a specific card in the Anki browser by its Arete ID.

        Searches for the card using its Arete tag (e.g. arete_01ARZ...).

        Args:
            arete_id: The Arete ID of the card (e.g. "arete_01ARZ3NDEKTSV4RRFFQ69G5FAV")

        """
        # Arete IDs are stored as tags on Anki notes
        query = f"tag:{arete_id}"
        bridge = await _bridge()
        ok = await bridge.gui_browse(query)
        if ok:
            return f"Opened Anki browser for card {arete_id}"
        return "Failed to open Anki browser. Is Anki running with AnkiConnect?"

    @mcp.tool()
    async def get_concept_cards(concept: str, deck: str = "") -> str:
        """Get flashcard content for a concept by reading vault markdown.

        Scans the vault for the concept note (e.g. "Hash Table.md"),
        extracts cards from YAML frontmatter, and returns their content.
        No Anki connection needed -- reads directly from vault files.

        Args:
            concept: The concept name (e.g. "Hash Table", "Binary Search Tree")
            deck: Optional deck filter -- only show cards in this deck

        """
        from arete.application.utils.text import parse_frontmatter

        config = _config()
        vault_root = config.vault_root
        if vault_root is None:
            return "Error: vault_root not configured"

        # Search for the concept note
        concept_path = _find_concept_file(vault_root, concept)
        if concept_path is None:
            return f"No vault note found for concept '{concept}'"

        text = concept_path.read_text(encoding="utf-8", errors="replace")
        meta, _ = parse_frontmatter(text)
        if not meta or "__yaml_error__" in meta:
            return f"Error parsing frontmatter in {concept_path.name}"

        cards = meta.get("cards", [])
        if not cards:
            return f"No cards found in {concept_path.name}"

        doc_deck = meta.get("deck", "")
        results = []
        for i, card in enumerate(cards, 1):
            if not isinstance(card, dict):
                continue
            card_deck = card.get("deck", doc_deck)
            if deck and card_deck and deck not in card_deck:
                continue
            results.append(_extract_card_entry(card, i, card_deck))

        if not results:
            return f"No cards matched in {concept_path.name}" + (
                f" (deck filter: {deck})" if deck else ""
            )

        return json.dumps(
            {
                "concept": concept,
                "file": concept_path.name,
                "card_count": len(results),
                "cards": results,
            },
            indent=2,
        )

    @mcp.tool()
    async def get_due_cards(
        deck: str = "",
        include_new: bool = False,
    ) -> str:
        """Show what cards are due for review.

        Returns due card count and their Arete IDs, optionally filtered by deck.

        Args:
            deck: Optional deck filter (e.g. "CS::DSA")
            include_new: Whether to include new (unreviewed) cards

        """
        bridge = await _bridge()
        nids = await bridge.get_due_cards(
            deck_name=deck if deck else None,
            include_new=include_new,
        )

        if not nids:
            msg = "No cards due for review"
            if deck:
                msg += f" in deck {deck}"
            if not include_new:
                msg += " (excluding new cards)"
            return msg

        # Map NIDs back to Arete IDs
        arete_ids = await bridge.map_nids_to_arete_ids(nids)

        return json.dumps(
            {
                "due_count": len(nids),
                "deck_filter": deck or "(all decks)",
                "include_new": include_new,
                "arete_ids": arete_ids,
            },
            indent=2,
        )

    @mcp.tool()
    async def build_study_queue(
        deck: str = "CS::DSA",
        depth: int = 2,
        include_new: bool = False,
    ) -> str:
        """Build a dependency-ordered study queue in Anki.

        Fetches due cards, resolves prerequisites using the vault's dependency
        graph, topologically sorts them, and creates a filtered deck in Anki.

        Args:
            deck: Deck to build queue from (default: "CS::DSA")
            depth: How many prerequisite levels to include (default: 2)
            include_new: Whether to include new (unreviewed) cards

        """
        from arete.application.queue.builder import build_simple_queue

        bridge = await _bridge()
        config = _config()
        vault_root = config.vault_root
        if vault_root is None:
            return "Error: vault_root not configured"

        # Get due cards
        nids = await bridge.get_due_cards(deck_name=deck, include_new=include_new)
        if not nids:
            return f"No cards due in deck {deck}"

        # Map to Arete IDs
        arete_ids = await bridge.map_nids_to_arete_ids(nids)
        valid_ids = [aid for aid in arete_ids if aid]
        if not valid_ids:
            return "Could not resolve any due cards to Arete IDs"

        # Build queue
        result = build_simple_queue(
            vault_root=vault_root,
            due_card_ids=valid_ids,
            depth=depth,
        )

        # Create filtered deck in Anki
        all_ordered = result.prereq_queue + result.main_queue
        if all_ordered:
            cids = await bridge.get_card_ids_for_arete_ids(all_ordered)
            valid_cids = [c for c in cids if c]
            if valid_cids:
                await bridge.create_topo_deck("Arete::Queue", valid_cids)

        return json.dumps(
            {
                "deck": deck,
                "due_cards": len(result.main_queue),
                "prereq_cards": len(result.prereq_queue),
                "total_queued": len(all_ordered),
                "missing_prereqs": result.missing_prereqs,
                "cycles": result.cycles,
                "queue_order": all_ordered,
            },
            indent=2,
        )

    return mcp


def _extract_card_entry(card: dict[str, Any], index: int, card_deck: str) -> dict[str, Any]:
    """Extract a card's display fields into a summary dict."""
    entry: dict[str, Any] = {"index": index}
    if card.get("id"):
        entry["arete_id"] = card["id"]
    if card.get("model"):
        entry["model"] = card["model"]
    entry["deck"] = card_deck

    # Content fields (case-insensitive first letter)
    for key in ("Front", "Back", "Text", "Back Extra"):
        value = card.get(key) or card.get(key.lower())
        if value:
            entry[key] = value

    deps = card.get("deps", {})
    if deps:
        entry["deps"] = deps
    return entry


def _find_concept_file(vault_root: Path, concept: str) -> Path | None:
    """Find a concept note in the vault by name.

    Tries exact match first, then case-insensitive search.
    """
    # Exact match
    exact = vault_root / f"{concept}.md"
    if exact.exists():
        return exact

    # Case-insensitive search in vault root
    concept_lower = concept.lower()
    for p in vault_root.iterdir():
        if p.suffix == ".md" and p.stem.lower() == concept_lower:
            return p

    # Search subdirectories (one level deep)
    for d in vault_root.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            for p in d.iterdir():
                if p.suffix == ".md" and p.stem.lower() == concept_lower:
                    return p

    return None


# Module-level server instance for the entry point
_server = create_server()


def main():
    """Entry point for MCP server."""
    _server.run(transport="stdio")


if __name__ == "__main__":
    main()
