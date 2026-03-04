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


class AppConfig(BaseSettings):
    """Professional configuration model for arete.
    Supports loading from:
    1. Environment variables (O2A_*)
    2. Config file (~/.config/arete/config.toml)
    3. Manual overrides (CLI)
    """

    model_config = SettingsConfigDict(
        env_prefix="O2A_",
        toml_file=[
            Path.home() / ".config/arete/config.toml",
            Path.home() / ".arete.toml",
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
    # Renamed from run_apy for clarity
    sync_enabled: bool = Field(default=False, alias="run")
    keep_going: bool = False
    no_move_deck: bool = False
    dry_run: bool = False
    prune: bool = False
    force: bool = False
    clear_cache: bool = False

    # Performance
    workers: int = Field(default_factory=lambda: max(1, cpu_count() // 2))
    queue_size: int = 4096
    verbose: int = 1

    # Internal UI Flags (usually CLI only)
    show_config: bool = False
    open_logs: bool = False
    open_config: bool = False

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

        # Try to load from both possible config locations
        toml_files = [
            Path.home() / ".config/arete/config.toml",
            Path.home() / ".arete.toml",
        ]

        # Find the first existing file
        toml_file = None
        for f in toml_files:
            if f.exists():
                toml_file = f
                break

        if toml_file:
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
    3. Environment variables (O2A_*)
    4. cli_overrides (passed from Typer)
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

    if config.vault_root is None:
        config.vault_root = (
            config.root_input if config.root_input.is_dir() else config.root_input.parent
        ).resolve()
    else:
        # Ensure vault_root is absolute
        config.vault_root = config.vault_root.resolve()
        # Heuristic: If root_input is NOT inside the configured vault_root,
        # then the configured vault_root (e.g. from ~/.config) is likely irrelevant for this run.
        # We should default to the root_input as the vault root.
        try:
            config.root_input.relative_to(config.vault_root)
        except ValueError:
            # root_input matches vault_root is fine (relative_to returns . or empty?)
            # relative_to raises ValueError if not relative.
            # So here we detect mismatch.
            config.vault_root = (
                config.root_input if config.root_input.is_dir() else config.root_input.parent
            ).resolve()

    if config.anki_media_dir is None or config.anki_base is None:
        detected_base, detected_media = detect_anki_paths()
        if config.anki_media_dir is None:
            config.anki_media_dir = detected_media
        if config.anki_base is None:
            config.anki_base = detected_base

    return config
