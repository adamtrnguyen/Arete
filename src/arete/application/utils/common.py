import os
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


def _detect_media_dir(base: Path) -> Path:
    """Return the first Anki profile's media dir under `base`, else `User 1/collection.media`."""
    try:
        if base.exists():
            profiles = sorted(
                (p for p in base.iterdir() if p.is_dir() and (p / "collection.anki2").exists()),
                key=lambda p: p.name,
            )
            if profiles:
                return profiles[0] / "collection.media"
    except OSError:
        pass
    return base / "User 1/collection.media"


def detect_anki_paths() -> tuple[Path | None, Path]:
    """Return (anki_base_dir, anki_media_dir)."""
    system = platform.system()

    if system == "Darwin":
        base = Path.home() / "Library/Application Support/Anki2"
        return (base, _detect_media_dir(base))

    if system == "Windows":
        # Anki keeps its folder in %APPDATA%, which a roaming or redirected profile moves.
        appdata = os.environ.get("APPDATA")
        base = (Path(appdata) if appdata else Path.home() / "AppData/Roaming") / "Anki2"
        return (base, _detect_media_dir(base))

    if system == "Linux":
        # Detect WSL
        if "microsoft" in platform.uname().release.lower():
            users_dir = Path("/mnt/c/Users")
            if users_dir.exists():
                candidates = sorted(
                    (
                        p
                        for p in users_dir.iterdir()
                        if p.is_dir()
                        and p.name not in {"Public", "Default", "Default User", "All Users"}
                    ),
                    key=lambda p: p.name,
                )
                if candidates:
                    win_home = candidates[0]
                    base = win_home / "AppData/Roaming/Anki2"
                    return (base, _detect_media_dir(base))

        base = Path.home() / ".local/share/Anki2"
        return (base, _detect_media_dir(base))

    return (None, Path("."))
