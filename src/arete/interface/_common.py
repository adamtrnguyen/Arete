"""Shared utilities for all CLI submodules."""

from arete.application.config import AppConfig, resolve_config


def _resolve_with_overrides(**kwargs) -> AppConfig:
    """Build config from keyword overrides, filtering out None values."""
    return resolve_config({k: v for k, v in kwargs.items() if v is not None})
