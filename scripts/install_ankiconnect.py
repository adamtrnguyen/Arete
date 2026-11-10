"""Prepare docker/anki_data for the Anki container: preferences, media folder, permissions.

The container mounts arete_ankiconnect as its only add-on (docker/docker-compose.yml).
"""

import os
from pathlib import Path


def install_ankiconnect():
    # Seed 'prefs21.db' to skip First Run Wizard
    # The container runs as user 1000 (abc), mapping $HOME to /config (which is docker/anki_data)
    # Location: docker/anki_data/.local/share/Anki2/prefs21.db
    fixture_path = Path(__file__).parent.parent / "tests/fixtures/anki/prefs21.db"
    if fixture_path.exists():
        print("Seeding Anki preferences (bypassing wizard)...")
        anki_base = Path(__file__).parent.parent / "docker/anki_data"
        prefs_dir = anki_base / ".local/share/Anki2"
        prefs_dir.mkdir(parents=True, exist_ok=True)

        import shutil

        shutil.copy(fixture_path, prefs_dir / "prefs21.db")
        print(f"Copied {fixture_path.name} to {prefs_dir}")
    else:
        print(f"⚠️ Warning: Fixture {fixture_path} not found. Anki may show setup wizard.")

    # Create collection.media directory for media files
    media_dir = (
        Path(__file__).parent.parent / "docker/anki_data/.local/share/Anki2/User 1/collection.media"
    )
    media_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created media directory: {media_dir}")

    # Ensure permissive permissions for Docker user (uid 1000)
    print("Fixing permissions...")
    start_dir = Path(__file__).parent.parent / "docker/anki_data"
    for root, dirs, files in os.walk(start_dir):
        for d in dirs:
            try:
                os.chmod(os.path.join(root, d), 0o777)
            except OSError:
                pass
        for f in files:
            try:
                os.chmod(os.path.join(root, f), 0o666)
            except OSError:
                pass
    print("Permissions set to 777/666 for anki_data.")


if __name__ == "__main__":
    install_ankiconnect()
