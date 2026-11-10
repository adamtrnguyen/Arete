from pathlib import Path

import pytest

from arete.application.config import VaultRootUnknown, require_vault_root, resolve_config


def test_config_defaults(mock_home, mock_vault):
    # Setup args
    overrides = {
        "root_input": str(mock_vault),
        "verbose": 0,
    }

    cfg = resolve_config(overrides)

    assert cfg.root_input == mock_vault.resolve()
    assert cfg.verbose == 0
    assert cfg.backend == "auto"


def test_config_file_override(mock_home, mock_vault):
    # Write a config file
    config_dir = mock_home / ".config/arete"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("verbose = 2\nkeep_going = true", encoding="utf-8")

    overrides = {
        "root_input": str(mock_vault),
        # Note: Pydantic doesn't treat False as an override for booleans
        # So we test that config file values are loaded
    }

    cfg = resolve_config(overrides, config_file=config_dir / "config.toml")
    assert cfg.verbose == 2  # From config
    assert cfg.keep_going is True  # From config file


def test_config_default_cwd_when_no_path(mock_home):
    # Setup args with no path
    overrides = {}

    cfg = resolve_config(overrides)
    assert cfg.root_input == Path.cwd().resolve()


def test_a_file_outside_the_configured_vault_and_in_no_vault_has_no_root(tmp_path):
    configured_root = tmp_path / "ConfigVault"
    configured_root.mkdir()
    input_path = tmp_path / "Other" / "file.md"
    input_path.parent.mkdir(parents=True)
    input_path.touch()

    cfg = resolve_config({"root_input": str(input_path), "vault_root": str(configured_root)})

    assert cfg.vault_root is None
    with pytest.raises(VaultRootUnknown, match=r"no folder above it holds \.obsidian"):
        require_vault_root(cfg)


def _obsidian_vault(tmp_path):
    vault = tmp_path / "My Vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "courses" / "stats").mkdir(parents=True)
    note = vault / "courses" / "stats" / "Bayes.md"
    note.touch()
    return vault.resolve(), note


def test_a_file_in_a_subfolder_belongs_to_the_vault_above_it(tmp_path):
    vault, note = _obsidian_vault(tmp_path)

    cfg = resolve_config({"root_input": str(note)})

    assert cfg.vault_root == vault
    assert cfg.root_input == note.resolve()


def test_a_subfolder_belongs_to_the_vault_above_it(tmp_path):
    """So a sync of the subfolder renders the vault's links and is never a whole-vault prune."""
    vault, note = _obsidian_vault(tmp_path)

    cfg = resolve_config({"root_input": str(note.parent)})

    assert cfg.vault_root == vault
    assert cfg.root_input != cfg.vault_root


def test_a_file_outside_the_configured_vault_belongs_to_its_own_vault(tmp_path):
    vault, note = _obsidian_vault(tmp_path)
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()

    cfg = resolve_config({"root_input": str(note), "vault_root": str(elsewhere)})

    assert cfg.vault_root == vault


def test_a_folder_in_no_vault_is_its_own_vault(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()

    cfg = resolve_config({"root_input": str(folder)})

    assert cfg.vault_root == folder.resolve()
