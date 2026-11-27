"""Seed docker/anki_data for the Anki container: preferences and media directory.

The container mounts arete_ankiconnect read-only as its only add-on
(docker/docker-compose.yml).
"""

import shutil
from pathlib import Path


def seed_anki_data() -> None:
    """Copy the fixture prefs21.db and ensure the Anki media directory exists."""
    repo_root = Path(__file__).resolve().parent.parent
    anki_base = repo_root / "docker" / "anki_data"
    fixture_path = repo_root / "tests" / "fixtures" / "anki" / "prefs21.db"

    if fixture_path.exists():
        print("Seeding Anki preferences (bypassing wizard)...")
        prefs_dir = anki_base / ".local" / "share" / "Anki2"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_path, prefs_dir / "prefs21.db")
        print(f"Copied {fixture_path.name} to {prefs_dir}")
    else:
        print(f"Warning: Fixture {fixture_path} not found. Anki may show setup wizard.")

    media_dir = anki_base / ".local" / "share" / "Anki2" / "User 1" / "collection.media"
    media_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created media directory: {media_dir}")


if __name__ == "__main__":
    seed_anki_data()
