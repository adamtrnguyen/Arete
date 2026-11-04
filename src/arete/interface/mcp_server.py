"""Arete MCP Server.

Exposes Arete sync and learning tools via the Model Context Protocol (MCP),
enabling AI agents (Claude, Gemini, etc.) to interact with Anki flashcards.
"""

from __future__ import annotations

import functools
import json
import logging
from importlib.metadata import version
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from arete.application.config import AppConfig, resolve_config
from arete.application.queue.service import QueueAlgo
from arete.application.vault_paths import configured_vault_root, within_vault
from arete.composition.factory import get_anki_bridge
from arete.composition.orchestrator import execute_sync
from arete.domain.interfaces import AnkiBridge

logger = logging.getLogger(__name__)


def _reported(fn):
    """Give the client the reason a tool failed.

    mcp 2.x forwards only ToolError text; any other exception (Anki unreachable, a bad
    config) arrived as a bare "Error executing tool X".
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


def create_server() -> MCPServer:  # noqa: C901
    """Create and configure the Arete MCP server with all tools."""
    mcp = MCPServer("arete", version=version("arete"))

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
    @_reported
    async def sync_vault(
        vault_path: str = "",
        force: bool = False,
        prune: bool = False,
        confirm_prune: bool = False,
    ) -> str:
        """Sync the Obsidian vault to Anki. Returns sync statistics.

        Args:
            vault_path: A folder inside the configured vault (optional; default: the whole
                vault). A path outside the configured vault is refused.
            force: Re-send every card, ignoring and clearing the content cache. It deletes
                nothing and does not confirm a prune.
            prune: Find Anki notes Arete created whose card is gone from the vault, and
                Arete decks that hold only such notes. Notes without an Arete id tag are
                never touched. Runs only when vault_path is the whole vault. Without
                confirm_prune this is a preview: the result's "prune" field lists what
                would be deleted, and nothing is deleted.
            confirm_prune: Delete what prune lists. Only set this after the user has seen
                the preview from a run without it and agreed.

        """
        root = configured_vault_root()
        target = within_vault(vault_path, root) if vault_path else root
        overrides: dict[str, Any] = {"vault_root": root, "root_input": target}
        if force:
            overrides["force"] = True
            overrides["clear_cache"] = True
        if prune:
            overrides["prune"] = True

        config = resolve_config(cli_overrides=overrides)
        stats = await execute_sync(config, confirm_prune=confirm_prune)

        return json.dumps(
            {
                "success": stats.total_errors == 0,
                "total_generated": stats.total_generated,
                "total_imported": stats.total_imported,
                "total_errors": stats.total_errors,
                "prune": stats.prune.to_dict() if stats.prune else None,
            },
            indent=2,
        )

    @mcp.tool()
    @_reported
    async def sync_file(
        file_path: str,
        force: bool = False,
    ) -> str:
        """Sync one Markdown file in the configured vault to Anki. Never prunes.

        Args:
            file_path: Path to the Markdown file, absolute or relative to the vault. A path
                outside the configured vault is refused.
            force: Re-send the file's cards, ignoring and clearing the content cache. It
                deletes nothing.

        """
        root = configured_vault_root()
        path = within_vault(file_path, root)
        if not path.exists():
            return f"Error: File not found: {file_path}"

        overrides: dict[str, Any] = {"vault_root": root, "root_input": path}
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
    @_reported
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
    @_reported
    async def check_graph(vault_path: str = "", deck: str = "") -> str:
        """Check dependency graph health: cycles, isolated cards, unresolved refs.

        Scans the vault for all Arete cards, builds the dependency graph,
        and reports structural issues. No Anki connection needed.

        Args:
            vault_path: A folder inside the configured vault (optional; default: the
                whole vault). A path outside it is refused.
            deck: Optional deck filter — only check cards in this deck

        """
        from dataclasses import asdict

        from arete.application.queue.graph_resolver import check_graph_health

        root = configured_vault_root()
        vault_root = within_vault(vault_path, root) if vault_path else root

        result = check_graph_health(vault_root, deck_filter=deck or None)
        return json.dumps(asdict(result), indent=2)

    @mcp.tool()
    @_reported
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
    @_reported
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
    @_reported
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
    @_reported
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
    @_reported
    async def build_study_queue(
        deck: str = "CS::DSA",
        depth: int = 2,
        include_new: bool = False,
        algo: QueueAlgo = "simple",
    ) -> str:
        """Build a dependency-ordered study queue in Anki.

        Fetches due cards, resolves prerequisites using the vault's dependency
        graph, topologically sorts them, and creates a filtered deck in Anki.

        Args:
            deck: Deck to build queue from (default: "CS::DSA")
            depth: How many prerequisite levels to include (default: 2)
            include_new: Whether to include new (unreviewed) cards
            algo: "simple" (default), "static" or "dynamic" -- same vocabulary as the CLI --algo

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
            algo=algo,
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
                "dropped_prereqs": result.dropped_prereqs,
                "cycles": result.cycles,
                "queue_order": all_ordered,
            },
            indent=2,
        )

    # ------------------------------------------------------------------
    # Agent-support tools (structured data for dep-wirer, auditors)
    # ------------------------------------------------------------------

    @mcp.tool()
    @_reported
    async def get_note_body(file_path: str) -> str:
        """Get only the markdown body of a note, stripping YAML frontmatter.

        Use this instead of Read when you already have card data from
        list_file_cards and only need the body content (definitions,
        intuition, related concepts, etc.).

        Args:
            file_path: Path to the markdown file, absolute or relative to the vault. A path
                outside the configured vault is refused.

        """
        from arete.application.card_reader import get_note_body as _get_note_body

        return _get_note_body(within_vault(file_path, configured_vault_root()))

    @mcp.tool()
    @_reported
    async def list_file_cards(file_path: str) -> str:
        """Extract all Arete cards from a markdown file as structured JSON.

        Returns each card's id, front/back (or Text for Cloze), model, deck,
        tags, and current deps. Agents should use this instead of reading
        raw markdown and parsing YAML themselves.

        Args:
            file_path: Path to the markdown file, absolute or relative to the vault. A path
                outside the configured vault is refused.

        """
        from dataclasses import asdict

        from arete.application.card_reader import list_file_cards as _list_file_cards

        result = _list_file_cards(within_vault(file_path, configured_vault_root()))
        if isinstance(result, str):
            return json.dumps({"error": result})
        return json.dumps(asdict(result), indent=2)

    @mcp.tool()
    @_reported
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
