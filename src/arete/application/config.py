from multiprocessing import cpu_count
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from arete.application.utils.common import detect_anki_paths


class VaultRootUnknown(ValueError):
    """No vault root is configured and none can be found above the path given."""

    def __init__(self, path: Path):
        """The message names the path and how to say which vault it belongs to."""
        super().__init__(
            f"Cannot tell which vault {path} belongs to: no folder above it holds .obsidian "
            "and no vault_root is configured. Set vault_root (config.toml or "
            "ARETE_VAULT_ROOT), or sync the vault folder."
        )
        self.path = path


def find_vault_root(path: Path) -> Path | None:
    """The nearest folder at or above `path` that holds `.obsidian`, or None."""
    start = path if path.is_dir() else path.parent
    return next((d for d in (start, *start.parents) if (d / ".obsidian").is_dir()), None)


def _guess_vault_root(root_input: Path) -> Path | None:
    """The vault of a path no configured vault root covers.

    The nearest folder holding `.obsidian`; failing that, a folder is its own vault and a
    file has none. A file's own folder is never guessed: a render under the wrong root
    names the wrong vault in every link and source field.
    """
    found = find_vault_root(root_input)
    if found is not None:
        return found.resolve()
    return root_input.resolve() if root_input.is_dir() else None


def require_vault_root(config: "AppConfig") -> Path:
    """The run's vault root; `VaultRootUnknown` when there is none."""
    if config.vault_root is None:
        raise VaultRootUnknown(config.root_input or Path.cwd())
    return config.vault_root


class AppConfig(BaseSettings):
    """Professional configuration model for arete.

    Supports loading from:
    1. Environment variables (ARETE_*)
    2. Config file (~/.config/arete/config.toml)
    3. Manual overrides (CLI).
    """

    model_config = SettingsConfigDict(
        env_prefix="ARETE_",
        toml_file=[
            Path.home() / ".config/arete/config.toml",
        ],
        extra="ignore",
    )

    # Paths
    root_input: Path | None = None
    vault_root: Path | None = None
    anki_media_dir: Path | None = None
    anki_base: Path | None = None
    cache_db: str | None = None
    log_dir: Path = Field(default_factory=lambda: Path.home() / ".config/arete/logs")

    # Execution Settings
    backend: Literal["auto", "direct", "ankiconnect"] = "auto"
    anki_connect_url: str = "http://127.0.0.1:8765"
    default_deck: str = "Default"

    # Flags
    keep_going: bool = False
    dry_run: bool = False
    prune: bool = False
    force: bool = False
    clear_cache: bool = False

    # Performance
    workers: int = Field(default_factory=lambda: max(1, cpu_count() // 2))
    queue_size: int = 4096
    verbose: int = 1

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        from pydantic_settings import TomlConfigSettingsSource

        toml_file = Path.home() / ".config/arete/config.toml"
        if toml_file.exists():
            # Priority order: CLI overrides > ENV vars > TOML config file
            return (
                init_settings,  # CLI overrides - HIGHEST priority
                env_settings,
                TomlConfigSettingsSource(settings_cls, toml_file=toml_file),
            )
        else:
            return (
                init_settings,  # CLI overrides - HIGHEST priority
                env_settings,
            )

    @field_validator("vault_root", mode="before")
    @classmethod
    def resolve_vault_root(cls, v: Any) -> Path | None:
        if v is None:
            return None
        return Path(v).resolve()

    @field_validator("anki_media_dir", mode="before")
    @classmethod
    def resolve_anki_media(cls, v: Any) -> Path | None:
        if v is None:
            return None
        return Path(v).resolve()

    @field_validator("anki_base", mode="before")
    @classmethod
    def resolve_anki_base(cls, v: Any) -> Path | None:
        if v:
            return Path(v).resolve()
        return None


def resolve_config(
    cli_overrides: dict[str, Any] | None = None, config_file: Path | None = None
) -> AppConfig:
    """Multi-layered configuration resolution.

    1. Defaults in AppConfig
    2. ~/.config/arete/config.toml (if exists) OR explicit config_file
    3. Environment variables (ARETE_*)
    4. cli_overrides (passed from Typer).
    """
    init_kwargs = cli_overrides or {}

    if config_file:
        from pydantic_settings import TomlConfigSettingsSource

        # Create a custom class on the fly or just instantiate with valid sources
        class ExplicitConfig(AppConfig):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                # Priority order: CLI overrides > ENV vars > TOML config file
                return (
                    init_settings,  # CLI overrides - HIGHEST priority
                    env_settings,
                    TomlConfigSettingsSource(settings_cls, toml_file=config_file),
                )

        config = ExplicitConfig(**init_kwargs)
    else:
        config = AppConfig(**init_kwargs)

    # Final cleanup of vault_root and anki_media_dir if they were missed during init
    if config.root_input is None:
        # No CLI path provided? Prefer configured vault_root, else CWD.
        config.root_input = config.vault_root if config.vault_root else Path.cwd()

    # Ensure root_input is absolute for relative_to checks
    config.root_input = config.root_input.resolve()

    # A configured vault root that holds the path is the vault. Otherwise the vault is
    # found from the path (`_guess_vault_root`); a file in no vault gets None, and a
    # command that needs the root refuses (`require_vault_root`).
    if config.vault_root is not None:
        config.vault_root = config.vault_root.resolve()
        if not config.root_input.is_relative_to(config.vault_root):
            config.vault_root = None
    if config.vault_root is None:
        config.vault_root = _guess_vault_root(config.root_input)

    if config.anki_media_dir is None or config.anki_base is None:
        detected_base, detected_media = detect_anki_paths()
        if config.anki_media_dir is None:
            config.anki_media_dir = detected_media
        if config.anki_base is None:
            config.anki_base = detected_base

    return config
