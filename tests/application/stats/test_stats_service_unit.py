from unittest.mock import AsyncMock

import pytest

from arete.application.stats.learning_insights_service import LearningInsightsService
from arete.domain.stats.models import LearningStats, NoteInsight


@pytest.fixture
def mock_anki_bridge():
    return AsyncMock()


@pytest.fixture
def stats_service(mock_anki_bridge):
    return LearningInsightsService(anki_bridge=mock_anki_bridge)


def test_clean_note_name_simple():
    name = "My Note"
    assert LearningInsightsService.clean_note_name(name) == "My Note"


def test_clean_note_name_md_extension():
    name = "My Note.md"
    assert LearningInsightsService.clean_note_name(name) == "My Note"


def test_clean_note_name_obsidian_source_format():
    # 'Vault|Path|ID' format
    name = "AreteVault|Folder/Subfolder/My Complex Note.md|12345"
    assert LearningInsightsService.clean_note_name(name) == "My Complex Note"


def test_clean_note_name_obsidian_source_simple_pipe():
    name = "AreteVault|Just Note.md"
    assert LearningInsightsService.clean_note_name(name) == "Just Note"


def test_clean_note_name_html_stripping():
    name = "<b>Bold Note</b>"
    assert LearningInsightsService.clean_note_name(name) == "Bold Note"


@pytest.mark.asyncio
async def test_get_learning_insights(stats_service, mock_anki_bridge):
    # Setup mock return
    mock_stats = LearningStats(
        total_cards=100,
        retention_rate=0.85,
        problematic_notes=[
            NoteInsight(
                note_name="Vault|Bad Note.md", issue="High lapse", lapses=5, deck="Default"
            ),
            NoteInsight(note_name="Good Note", issue="Leech", lapses=8, deck="Default"),
        ],
    )
    mock_anki_bridge.get_learning_insights.return_value = mock_stats

    # Execute
    result = await stats_service.get_learning_insights(lapse_threshold=4)

    # Verify
    mock_anki_bridge.get_learning_insights.assert_awaited_once_with(lapse_threshold=4)
    assert result.total_cards == 100

    # Verify cleaning happened
    assert result.problematic_notes[0].note_name == "Bad Note"
    assert result.problematic_notes[1].note_name == "Good Note"


@pytest.mark.asyncio
async def test_get_learning_insights_error(stats_service, mock_anki_bridge):
    mock_anki_bridge.get_learning_insights.side_effect = Exception("Anki Error")

    with pytest.raises(Exception, match="Anki Error"):
        await stats_service.get_learning_insights()
