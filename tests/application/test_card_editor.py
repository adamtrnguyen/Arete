"""Tests for card_editor maturity guard and editing operations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from arete.application.card_editor import (
    add_card,
    check_edit_policy,
    classify_maturity,
    delete_card,
    edit_body,
    edit_card,
)
from arete.domain.models import AnkiCardStats

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_NOTE = """\
---
arete: true
deck: AI::Deep Learning
model: Basic
tags:
- d2l
cards:
- id: arete_01AAA
  Front: What is backpropagation?
  Back: A method for computing gradients via chain rule.
  deps:
    requires: []
    related: []
  anki:
    nid: '12345'
- id: arete_01BBB
  Front: What is gradient descent?
  Back: An optimization algorithm that updates parameters.
  deps:
    requires:
    - arete_01AAA
    related: []
  anki:
    nid: '67890'
- id: arete_01CCC
  Front: What is a new card?
  Back: This card has no NID.
  deps:
    requires: []
    related: []
---

> [!definition] Backpropagation
> The algorithm for computing gradients in neural networks.

## Intuition

Backpropagation applies the chain rule of calculus.

## Related Concepts

- [[Gradient Descent]] — the optimizer that uses the gradients
"""

NEW_CARD_NOTE = """\
---
arete: true
deck: Math::Linear Algebra
model: Basic
tags: []
cards: []
---

> [!definition] Vector Space
> A set with vector addition and scalar multiplication.
"""


def _write_note(tmp_path: Path, content: str = SAMPLE_NOTE) -> Path:
    f = tmp_path / "Test Note.md"
    f.write_text(content, encoding="utf-8")
    return f


def _mock_bridge(intervals: dict[int, int]) -> AsyncMock:
    """Create a mock bridge that returns controlled intervals.

    intervals: {nid: interval_days}
    """
    bridge = AsyncMock()

    async def get_card_stats(nids):
        stats = []
        for nid in nids:
            if nid in intervals:
                stats.append(
                    AnkiCardStats(
                        card_id=nid + 1000,
                        note_id=nid,
                        lapses=0,
                        ease=2500,
                        difficulty=None,
                        deck_name="Test",
                        interval=intervals[nid],
                        due=0,
                        reps=10,
                    )
                )
        return stats

    bridge.get_card_stats = AsyncMock(side_effect=get_card_stats)
    return bridge


# ---------------------------------------------------------------------------
# classify_maturity
# ---------------------------------------------------------------------------


class TestClassifyMaturity:
    def test_mature(self):
        assert classify_maturity(30) == "mature"
        assert classify_maturity(22) == "mature"

    def test_young(self):
        assert classify_maturity(21) == "young"
        assert classify_maturity(1) == "young"

    def test_new(self):
        assert classify_maturity(0) == "new"
        assert classify_maturity(-1) == "new"


# ---------------------------------------------------------------------------
# check_edit_policy
# ---------------------------------------------------------------------------


class TestCheckEditPolicy:
    def test_mature_front_warned(self):
        assert check_edit_policy("mature", "Front") == "warned"

    def test_mature_back_warned(self):
        assert check_edit_policy("mature", "Back") == "warned"

    def test_mature_deps_allowed(self):
        assert check_edit_policy("mature", "deps") == "allowed"

    def test_young_front_warned(self):
        assert check_edit_policy("young", "Front") == "warned"

    def test_young_back_allowed(self):
        assert check_edit_policy("young", "Back") == "allowed"

    def test_new_everything_allowed(self):
        assert check_edit_policy("new", "Front") == "allowed"
        assert check_edit_policy("new", "Back") == "allowed"
        assert check_edit_policy("new", "deps") == "allowed"

    def test_tags_always_allowed(self):
        assert check_edit_policy("mature", "tags") == "allowed"
        assert check_edit_policy("young", "tags") == "allowed"
        assert check_edit_policy("new", "tags") == "allowed"


# ---------------------------------------------------------------------------
# edit_body
# ---------------------------------------------------------------------------


class TestEditBody:
    @pytest.mark.anyio
    async def test_edit_body_success(self, tmp_path: Path):
        f = _write_note(tmp_path)
        result = await edit_body(f, "chain rule of calculus", "chain rule")
        assert result.success
        assert "chain rule" in f.read_text()
        assert "chain rule of calculus" not in f.read_text()

    @pytest.mark.anyio
    async def test_edit_body_not_found(self, tmp_path: Path):
        f = _write_note(tmp_path)
        result = await edit_body(f, "nonexistent text", "replacement")
        assert not result.success
        assert "not found" in result.message

    @pytest.mark.anyio
    async def test_edit_body_rejects_frontmatter(self, tmp_path: Path):
        f = _write_note(tmp_path)
        result = await edit_body(f, "What is backpropagation?", "Changed front")
        assert not result.success
        assert "frontmatter" in result.message

    @pytest.mark.anyio
    async def test_edit_empty_body(self, tmp_path: Path):
        f = _write_note(tmp_path, NEW_CARD_NOTE)
        body_content = "\n> [!definition] Vector Space\n> A set with operations.\n\n## Intuition\n\nA vector space is fundamental.\n"
        result = await edit_body(f, "", body_content)
        assert result.success
        content = f.read_text()
        assert "A set with operations." in content
        assert "arete: true" in content  # frontmatter preserved

    @pytest.mark.anyio
    async def test_edit_body_preserves_frontmatter(self, tmp_path: Path):
        f = _write_note(tmp_path)
        await edit_body(f, "chain rule of calculus", "chain rule")
        new_text = f.read_text()
        # Frontmatter cards should be preserved
        assert "arete_01AAA" in new_text
        assert "What is backpropagation?" in new_text


# ---------------------------------------------------------------------------
# edit_card — maturity enforcement
# ---------------------------------------------------------------------------


class TestEditCard:
    @pytest.mark.anyio
    async def test_mature_front_warned(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 30})  # mature
        result = await edit_card(f, 0, {"Front": "New front"}, bridge=bridge)
        assert result.success
        assert "Front" in result.warned
        assert "Front" in result.applied
        assert result.maturity == "mature"
        assert "New front" in f.read_text()

    @pytest.mark.anyio
    async def test_mature_back_warned(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 30})
        result = await edit_card(f, 0, {"Back": "Improved answer."}, bridge=bridge)
        assert result.success
        assert "Back" in result.warned
        assert "Back" in result.applied
        assert "Improved answer." in f.read_text()

    @pytest.mark.anyio
    async def test_mature_deps_allowed(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 30})
        new_deps = {"requires": ["Vector Space"], "related": []}
        result = await edit_card(f, 0, {"deps": new_deps}, bridge=bridge)
        assert result.success
        assert "deps" in result.applied
        assert result.warned == []
        assert result.blocked == []

    @pytest.mark.anyio
    async def test_young_front_warned(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 10})
        result = await edit_card(f, 0, {"Front": "Revised front"}, bridge=bridge)
        assert result.success
        assert "Front" in result.warned
        assert "Front" in result.applied

    @pytest.mark.anyio
    async def test_new_card_everything_allowed(self, tmp_path: Path):
        f = _write_note(tmp_path)
        # Card at index 2 has no NID
        result = await edit_card(f, 2, {"Front": "New front", "Back": "New back"})
        assert result.success
        assert result.maturity == "new"
        assert result.warned == []
        assert result.blocked == []

    @pytest.mark.anyio
    async def test_force_overrides_block(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 30})
        result = await edit_card(f, 0, {"Front": "Forced front"}, bridge=bridge, force=True)
        assert result.success
        assert "Front" in result.applied
        assert "Forced front" in f.read_text()

    @pytest.mark.anyio
    async def test_invalid_field_rejected(self, tmp_path: Path):
        f = _write_note(tmp_path)
        result = await edit_card(f, 0, {"bogus_field": "value"})
        assert not result.success
        assert "Invalid field" in result.message

    @pytest.mark.anyio
    async def test_invalid_index(self, tmp_path: Path):
        f = _write_note(tmp_path)
        result = await edit_card(f, 99, {"Back": "text"})
        assert not result.success
        assert "Invalid card_index" in result.message

    @pytest.mark.anyio
    async def test_offline_defaults_to_mature(self, tmp_path: Path):
        """When bridge is None (Anki offline), cards with NIDs are treated as mature."""
        f = _write_note(tmp_path)
        result = await edit_card(f, 0, {"Front": "Should be warned"}, bridge=None)
        assert result.success
        assert result.maturity == "mature"
        assert "Front" in result.warned

    @pytest.mark.anyio
    async def test_stats_failure_is_unknown_not_mature(self, tmp_path: Path):
        """A bridge that errors must not turn a brand-new card into a 'mature' one."""
        from unittest.mock import AsyncMock

        f = _write_note(tmp_path)
        bridge = AsyncMock()
        bridge.get_card_stats.side_effect = RuntimeError("AnkiConnect timeout")
        result = await edit_card(f, 0, {"Front": "Edited"}, bridge=bridge)
        assert result.success
        assert result.maturity == "unknown"
        assert result.warned == []


# ---------------------------------------------------------------------------
# add_card
# ---------------------------------------------------------------------------


class TestAddCard:
    @pytest.mark.anyio
    async def test_add_card_success(self, tmp_path: Path):
        f = _write_note(tmp_path, NEW_CARD_NOTE)
        card = {"Front": "What is a vector?", "Back": "An element of a vector space."}
        result = await add_card(f, card)
        assert result.success
        assert result.index == 0
        assert result.arete_id.startswith("arete_")
        content = f.read_text()
        assert "What is a vector?" in content

    @pytest.mark.anyio
    async def test_add_card_auto_deps(self, tmp_path: Path):
        f = _write_note(tmp_path, NEW_CARD_NOTE)
        card = {"Front": "Q?", "Back": "A."}
        result = await add_card(f, card)
        assert result.success
        # Re-read and check deps were added
        from arete.application.utils.text import parse_frontmatter

        meta, _ = parse_frontmatter(f.read_text())
        added_card = meta["cards"][0]
        assert "deps" in added_card
        assert added_card["deps"]["requires"] == []

    @pytest.mark.anyio
    async def test_add_card_preserves_existing(self, tmp_path: Path):
        f = _write_note(tmp_path)
        card = {"Front": "New Q?", "Back": "New A."}
        result = await add_card(f, card)
        assert result.success
        assert result.index == 3  # 3 existing cards, so new one is at index 3
        content = f.read_text()
        assert "What is backpropagation?" in content  # existing card preserved


# ---------------------------------------------------------------------------
# delete_card
# ---------------------------------------------------------------------------


class TestDeleteCard:
    @pytest.mark.anyio
    async def test_delete_new_card_succeeds(self, tmp_path: Path):
        f = _write_note(tmp_path)
        # Card at index 2 has no NID → "new" maturity → allowed
        result = await delete_card(f, 2, bridge=None)
        assert result.success
        from arete.application.utils.text import parse_frontmatter

        meta, _ = parse_frontmatter(f.read_text())
        assert len(meta["cards"]) == 2

    @pytest.mark.anyio
    async def test_delete_mature_blocked(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 30})
        result = await delete_card(f, 0, bridge=bridge)
        assert not result.success
        assert "mature" in result.message

    @pytest.mark.anyio
    async def test_delete_mature_forced(self, tmp_path: Path):
        f = _write_note(tmp_path)
        bridge = _mock_bridge({12345: 30})
        result = await delete_card(f, 0, bridge=bridge, force=True)
        assert result.success
        from arete.application.utils.text import parse_frontmatter

        meta, _ = parse_frontmatter(f.read_text())
        assert len(meta["cards"]) == 2

    @pytest.mark.anyio
    async def test_delete_invalid_index(self, tmp_path: Path):
        f = _write_note(tmp_path)
        result = await delete_card(f, 99)
        assert not result.success
