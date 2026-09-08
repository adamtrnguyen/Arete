from pathlib import Path

from arete.application.config import resolve_config


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


def test_config_resets_vault_root_if_input_outside(tmp_path):
    # configured vault root
    configured_root = tmp_path / "ConfigVault"
    configured_root.mkdir()

    # input path outside
    input_path = tmp_path / "Other" / "file.md"
    input_path.parent.mkdir(parents=True)
    input_path.touch()

    overrides = {
        "root_input": str(input_path),
        "vault_root": str(configured_root),
    }

    cfg = resolve_config(overrides)

    # Heuristic should kick in: input is not relative to configured root
    # So vault_root should reset to input's parent
    assert cfg.vault_root == input_path.parent.resolve()
