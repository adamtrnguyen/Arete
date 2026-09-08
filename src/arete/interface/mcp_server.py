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
from arete.composition.factory import get_anki_bridge
from arete.composition.orchestrator import execute_sync
from arete.domain.interfaces import AnkiBridge

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
    async def check_graph(vault_path: str = "", deck: str = "") -> str:
        """Check dependency graph health: cycles, isolated cards, unresolved refs.

        Scans the vault for all Arete cards, builds the dependency graph,
        and reports structural issues. No Anki connection needed.

        Args:
            vault_path: Path to vault (optional, uses config default)
            deck: Optional deck filter — only check cards in this deck

        """
        from dataclasses import asdict

        from arete.application.queue.graph_resolver import check_graph_health

        config = _config()
        vault_root = Path(vault_path) if vault_path else config.vault_root
        if vault_root is None:
            return "Error: vault_root not configured"

        result = check_graph_health(vault_root, deck_filter=deck or None)
        return json.dumps(asdict(result), indent=2)

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
        from dataclasses import asdict

        from arete.application.card_reader import get_concept_cards as _get_concept_cards

        config = _config()
        vault_root = config.vault_root
        if vault_root is None:
            return "Error: vault_root not configured"

        result = _get_concept_cards(vault_root, concept, deck_filter=deck)
        if isinstance(result, str):
            return result
        return json.dumps(asdict(result), indent=2)

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
        from arete.application.queue.service import build_study_queue as _build_queue

        bridge = await _bridge()
        config = _config()
        vault_root = config.vault_root
        if vault_root is None:
            return "Error: vault_root not configured"

        result = await _build_queue(
            bridge,
            vault_root,
            deck=deck,
            depth=depth,
            include_new=include_new,
            algo="simple",
            enrich=False,
        )

        if result.due_count == 0:
            return f"No cards due in deck {deck}"

        all_ordered = [item.id for item in result.queue_items]

        return json.dumps(
            {
                "deck": deck,
                "due_cards": result.main_count,
                "prereq_cards": result.prereq_count,
                "total_queued": result.total_queued,
                "missing_prereqs": result.missing_prereqs,
                "cycles": result.cycles,
                "queue_order": all_ordered,
            },
            indent=2,
        )

    # ------------------------------------------------------------------
    # Agent-support tools (structured data for dep-wirer, auditors)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_note_body(file_path: str) -> str:
        """Get only the markdown body of a note, stripping YAML frontmatter.

        Use this instead of Read when you already have card data from
        list_file_cards and only need the body content (definitions,
        intuition, related concepts, etc.).

        Args:
            file_path: Absolute path to the markdown file

        """
        from arete.application.card_reader import get_note_body as _get_note_body

        return _get_note_body(Path(file_path))

    @mcp.tool()
    async def list_file_cards(file_path: str) -> str:
        """Extract all Arete cards from a markdown file as structured JSON.

        Returns each card's id, front/back (or Text for Cloze), model, deck,
        tags, and current deps. Agents should use this instead of reading
        raw markdown and parsing YAML themselves.

        Args:
            file_path: Absolute path to the markdown file

        """
        from dataclasses import asdict

        from arete.application.card_reader import list_file_cards as _list_file_cards

        result = _list_file_cards(Path(file_path))
        if isinstance(result, str):
            return json.dumps({"error": result})
        return json.dumps(asdict(result), indent=2)

    @mcp.tool()
    async def get_dep_subgraph(file_paths: str) -> str:
        """Build a dependency subgraph for a batch of files.

        Returns all cards in the given files with their deps, plus edges
        to/from cards outside the batch (external deps). Useful for the
        dep-wirer agent to see the full picture before editing.

        Args:
            file_paths: Comma-separated absolute paths to markdown files

        """
        from dataclasses import asdict

        from arete.application.queue.graph_resolver import get_subgraph_for_files

        config = _config()
        vault_root = config.vault_root
        if vault_root is None:
            return json.dumps({"error": "vault_root not configured"})

        paths = [p.strip() for p in file_paths.split(",") if p.strip()]
        result = get_subgraph_for_files(vault_root, paths)
        return json.dumps(asdict(result), indent=2)

    return mcp


# Module-level server instance for the entry point
_server = create_server()


def main():
    """Entry point for MCP server."""
    _server.run(transport="stdio")


if __name__ == "__main__":
    main()
