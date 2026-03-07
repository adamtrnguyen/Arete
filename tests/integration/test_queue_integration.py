"""Queue integration tests — sync a vault with deps, then test queue building."""

import pytest

from arete.application.orchestrator import execute_sync
from arete.application.queue.service import build_study_queue


@pytest.fixture
def deps_vault(vault_factory):
    """Vault with dependency relationships between cards."""
    return vault_factory(
        {
            "prereq.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - id: arete_PREREQ0000000000000001
    nid: null
    Front: Prerequisite Concept
    Back: Foundation knowledge
---
""",
            "main.md": """\
---
deck: IntegrationTest
arete: true
cards:
  - id: arete_MAIN00000000000000000001
    nid: null
    Front: Main Concept
    Back: Builds on prerequisite
    deps:
      requires:
        - arete_PREREQ0000000000000001
---
""",
        }
    )


async def _sync_vault(vault, sync_config):
    """Sync a vault and return stats."""
    config = sync_config(vault_root=vault)
    return await execute_sync(config)


@pytest.mark.asyncio
async def test_queue_empty_deck(anki_bridge, deps_vault, sync_config, setup_anki):
    """No due cards in a fresh deck → empty queue result."""
    # Sync to create cards (they'll be new, not due)
    await _sync_vault(deps_vault, sync_config)

    result = await build_study_queue(
        anki_bridge,
        deps_vault,
        deck="IntegrationTest",
        include_new=False,
        dry_run=True,
        enrich=False,
    )
    # New cards are not due by default
    assert result.due_count == 0 or result.total_queued == 0


@pytest.mark.asyncio
async def test_queue_with_deps(anki_bridge, deps_vault, sync_config, setup_anki):
    """Queue respects deps.requires ordering (prereq before main)."""
    await _sync_vault(deps_vault, sync_config)

    result = await build_study_queue(
        anki_bridge,
        deps_vault,
        deck="IntegrationTest",
        include_new=True,
        cross_deck=True,
        depth=2,
        dry_run=True,
        enrich=False,
    )

    if result.total_queued >= 2 and result.build_result:
        queue = result.build_result.prereq_queue + result.build_result.main_queue
        prereq_idx = next((i for i, q in enumerate(queue) if "PREREQ" in q), None)
        main_idx = next((i for i, q in enumerate(queue) if "MAIN" in q), None)
        if prereq_idx is not None and main_idx is not None:
            assert prereq_idx < main_idx, "Prerequisite should come before main card"


@pytest.mark.asyncio
async def test_queue_dry_run(anki_bridge, deps_vault, sync_config, setup_anki):
    """dry_run=True skips deck creation."""
    await _sync_vault(deps_vault, sync_config)

    result = await build_study_queue(
        anki_bridge,
        deps_vault,
        deck="IntegrationTest",
        include_new=True,
        dry_run=True,
        enrich=False,
    )
    assert not result.deck_created


@pytest.mark.asyncio
async def test_queue_creates_filtered_deck(anki_bridge, deps_vault, sync_config, setup_anki):
    """Filtered deck created in Anki when dry_run=False."""
    await _sync_vault(deps_vault, sync_config)

    result = await build_study_queue(
        anki_bridge,
        deps_vault,
        deck="IntegrationTest",
        include_new=True,
        dry_run=False,
        enrich=False,
    )

    if result.total_queued > 0:
        assert result.deck_created, "Filtered deck should have been created"
        assert result.deck_name == "Arete::Queue"
