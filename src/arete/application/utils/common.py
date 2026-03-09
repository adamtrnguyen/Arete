import platform
from pathlib import Path
from typing import Any


def to_list(x: Any) -> list[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]


def sanitize(v: Any) -> str:
    return "" if v is None else str(v).rstrip()


def detect_anki_paths() -> tuple[Path | None, Path]:
    """Return (anki_base_dir, anki_media_dir)."""
    system = platform.system()

    if system == "Darwin":
        base = Path.home() / "Library/Application Support/Anki2"
        return (base, base / "User 1/collection.media")

    if system == "Windows":
        base = Path.home() / "AppData/Roaming/Anki2"
        # Try to find a profile
        if base.exists():
            profiles = [
                p for p in base.iterdir() if p.is_dir() and (p / "collection.anki2").exists()
            ]
            if profiles:
                # Pick the first one (usually User 1 or the main one)
                return (base, profiles[0] / "collection.media")
        return (base, base / "User 1/collection.media")

    if system == "Linux":
        # Detect WSL
        if "microsoft" in platform.uname().release.lower():
            users_dir = Path("/mnt/c/Users")
            if users_dir.exists():
                candidates = [
                    p
                    for p in users_dir.iterdir()
                    if p.is_dir()
                    and p.name not in {"Public", "Default", "Default User", "All Users"}
                ]
                if candidates:
                    win_home = candidates[0]
                    base = win_home / "AppData/Roaming/Anki2"
                    return (base, base / "User 1/collection.media")

        base = Path.home() / ".local/share/Anki2"
        return (base, base / "User 1/collection.media")

    return (None, Path("."))
