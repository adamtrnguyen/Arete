"""Caller-supplied paths must resolve inside the configured vault."""

from pathlib import Path

import pytest

from arete.application.vault_paths import (
    PathRefusedError,
    configured_vault_root,
    require_configured,
    within_vault,
)


def test_the_vault_and_files_in_it_are_accepted(configured_vault):
    note = configured_vault / "Sub Folder" / "A note.md"
    note.parent.mkdir()
    note.write_text("x")
    assert within_vault(configured_vault, configured_vault) == configured_vault
    assert within_vault(str(note), configured_vault) == note


def test_a_relative_path_is_taken_relative_to_the_vault(configured_vault):
    assert within_vault("Sub/x.md", configured_vault) == configured_vault / "Sub" / "x.md"


@pytest.mark.parametrize("outside", ["/etc/passwd", "/", "../escape.md", "Sub/../../x.md"])
def test_a_path_outside_the_vault_is_refused(configured_vault, outside):
    with pytest.raises(PathRefusedError, match="outside the configured vault"):
        within_vault(outside, configured_vault)


def test_a_sibling_whose_name_extends_the_vault_name_is_refused(configured_vault):
    sibling = configured_vault.parent / (configured_vault.name + " Copy")
    sibling.mkdir()
    with pytest.raises(PathRefusedError):
        within_vault(sibling / "x.md", configured_vault)


def test_a_symlink_out_of_the_vault_is_refused(configured_vault, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("x")
    (configured_vault / "link.md").symlink_to(secret)
    with pytest.raises(PathRefusedError):
        within_vault(configured_vault / "link.md", configured_vault)


def test_paths_compare_resolved_so_a_symlinked_tmp_still_matches(tmp_path):
    """On macOS /tmp is a symlink to /private/tmp; either spelling names the same vault."""
    real = tmp_path / "real vault"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    (real / "n.md").write_text("x")
    assert within_vault(alias / "n.md", real) == (real / "n.md").resolve()
    assert within_vault(real / "n.md", alias) == (real / "n.md").resolve()


def test_with_no_vault_configured_nothing_is_accepted():
    with pytest.raises(PathRefusedError, match="No vault is configured"):
        configured_vault_root()


def test_the_configured_vault_comes_from_the_environment(configured_vault):
    assert configured_vault_root() == configured_vault


def test_require_configured_accepts_only_the_configured_path(tmp_path):
    base = tmp_path / "Anki2"
    base.mkdir()
    require_configured(None, base, "anki_base")
    require_configured(str(base), base, "anki_base")
    with pytest.raises(PathRefusedError, match="differs from the configured anki_base"):
        require_configured(str(tmp_path / "evil"), base, "anki_base")
    with pytest.raises(PathRefusedError):
        require_configured(str(base), None, "anki_base")


def test_the_cli_does_not_use_the_check():
    """The user-run CLI may sync any path; only the agent and HTTP surfaces are fenced."""
    cli = Path(__file__).parents[2] / "src" / "arete" / "interface" / "cli.py"
    assert "vault_paths" not in cli.read_text()
