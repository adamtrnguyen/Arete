"""Tests for arete.interface.wizard using direct dependency injection."""

import tomllib
from pathlib import Path
from unittest.mock import patch

from arete.interface.wizard import run_init_wizard


def _feed(answers: list[str]):
    it = iter(answers)
    return lambda _prompt="": next(it)


def test_wizard_default_flow(tmp_path: Path):
    home = tmp_path / "home"
    (home / "Obsidian Vault").mkdir(parents=True)
    detected_media = tmp_path / "anki" / "media"
    detected_media.mkdir(parents=True)

    run_init_wizard(
        input_fn=_feed(["", "", ""]),
        detect_paths=lambda: (tmp_path / "anki", detected_media),
        home_dir=home,
    )

    config_file = home / ".config/arete/config.toml"
    assert config_file.exists()
    parsed = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert parsed["vault_root"] == str((home / "Obsidian Vault").resolve())
    assert parsed["anki_media_dir"] == str(detected_media.resolve())
    assert parsed["backend"] == "auto"


def test_wizard_custom_flow(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    vault = tmp_path / "custom" / "vault"
    media = tmp_path / "custom" / "media"
    vault.mkdir(parents=True)
    media.mkdir(parents=True)

    run_init_wizard(
        input_fn=_feed([str(vault), str(media), "2"]),
        detect_paths=lambda: (None, None),
        home_dir=home,
    )

    parsed = tomllib.loads((home / ".config/arete/config.toml").read_text(encoding="utf-8"))
    assert parsed["vault_root"] == str(vault.resolve())
    assert parsed["anki_media_dir"] == str(media.resolve())
    assert parsed["backend"] == "ankiconnect"


def test_wizard_overwrite_no(tmp_path: Path):
    home = tmp_path / "home"
    (home / "Obsidian Vault").mkdir(parents=True)
    config_dir = home / ".config/arete"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("old config", encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()

    run_init_wizard(
        input_fn=_feed(["", str(media), "", "n"]),
        detect_paths=lambda: (None, None),
        home_dir=home,
    )

    assert (config_dir / "config.toml").read_text(encoding="utf-8") == "old config"


def test_wizard_path_validation_and_reprompt(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    v_good = tmp_path / "good_v"
    v_good.mkdir()
    m = tmp_path / "m"
    m.mkdir()

    run_init_wizard(
        input_fn=_feed([str(v_good), "", str(m), "1"]),
        detect_paths=lambda: (None, None),
        home_dir=home,
    )
    assert (home / ".config/arete/config.toml").exists()


def test_wizard_nonexistent_confirm(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    v_bad = tmp_path / "bad_v"
    m_bad = tmp_path / "bad_m"

    run_init_wizard(
        input_fn=_feed([str(v_bad), "y", str(m_bad), "y", "1"]),
        detect_paths=lambda: (None, None),
        home_dir=home,
    )
    assert (home / ".config/arete/config.toml").exists()


def test_wizard_write_error(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    v = tmp_path / "v"
    v.mkdir()
    m = tmp_path / "m"
    m.mkdir()
    # A directory at config.toml makes opening it for writing fail with IsADirectoryError.
    (home / ".config/arete/config.toml").mkdir(parents=True)

    ok = run_init_wizard(
        input_fn=_feed([str(v), str(m), "1", "y"]),
        detect_paths=lambda: (None, None),
        home_dir=home,
    )
    assert ok is False
    assert (home / ".config/arete/config.toml").is_dir()


def test_wizard_reprompts_invalid_backend_and_handles_eof(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    v = tmp_path / "v"
    v.mkdir()
    m = tmp_path / "m"
    m.mkdir()

    ok = run_init_wizard(
        input_fn=_feed([str(v), str(m), "9", "direct"]),
        detect_paths=lambda: (None, None),
        home_dir=home,
    )
    assert ok is True
    parsed = tomllib.loads((home / ".config/arete/config.toml").read_text(encoding="utf-8"))
    assert parsed["backend"] == "direct"

    def raise_eof(_prompt: str = "") -> str:
        raise EOFError

    assert (
        run_init_wizard(input_fn=raise_eof, detect_paths=lambda: (None, None), home_dir=home)
        is True
    )


def test_wizard_escapes_windows_backslashes_as_valid_toml(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()

    with patch(
        "arete.interface.wizard._ask_path",
        side_effect=[r"C:\Users\adam\Vault", r"C:\Users\adam\Anki\media"],
    ):
        run_init_wizard(
            input_fn=_feed(["1"]),
            detect_paths=lambda: (None, None),
            home_dir=home,
        )

    raw = (home / ".config/arete/config.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    assert parsed["vault_root"] == r"C:\Users\adam\Vault"
    assert parsed["anki_media_dir"] == r"C:\Users\adam\Anki\media"
