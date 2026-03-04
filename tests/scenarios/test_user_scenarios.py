from pathlib import Path

import pytest
from typer.testing import CliRunner

from arete.interface.cli import app

runner = CliRunner()

# Define scenarios: output_file -> expected_error_substring
# These files exist in mock_vault and are expected to FAIL validation.
SCENARIOS = [
    ("Tab Error.md", "Tab Character Error"),
    ("Tab in List.md", "Tab Character Error"),
    ("Duplicate Key.md", "Duplicate Key Error"),
    ("Bad Indent.md", "expected <block end>, but found"),
    ("Bad List.md", "Invalid format for 'cards'"),
    ("Mixed Content.md", "dictionary (key: value)"),  # Expects dict, got str
    ("Null Card.md", "got NoneType"),  # Should be "NoneType" not "empty"
    ("Missing Keys.md", "Missing 'Front'?"),
    ("Syntax in List.md", "expected ',' or ']'"),  # Standard PyYAML syntax error
]


@pytest.mark.parametrize("filename, expected_msg", SCENARIOS)
def test_mock_vault_scenarios(filename, expected_msg):
    """Ensures that known 'bad' files in the mock_vault are correctly flagging checks."""
    vault_path = Path("tests/mock_vault")
    file_path = vault_path / filename

    if not file_path.exists():
        pytest.skip(f"Mock file {filename} not found at {file_path}")

    result = runner.invoke(app, ["vault", "check", str(file_path)])

    # Assert failure
    assert result.exit_code == 1, f"{filename} should have failed validation but passed."

    # Assert specific error message
    assert expected_msg in result.stdout, (
        f"{filename} failed but message was wrong.\nGot: {result.stdout}"
    )
