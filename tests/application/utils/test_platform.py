from pathlib import Path
from unittest.mock import MagicMock, patch

from arete.application.utils.common import detect_anki_paths


def test_detect_paths_macos():
    with patch("platform.system", return_value="Darwin"):
        with patch("pathlib.Path.home", return_value=Path("/Users/test")):
            base, media = detect_anki_paths()
            assert base == Path("/Users/test/Library/Application Support/Anki2")
            assert media == Path(
                "/Users/test/Library/Application Support/Anki2/User 1/collection.media"
            )


def test_detect_paths_windows(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    with patch("platform.system", return_value="Windows"):
        with patch("pathlib.Path.home", return_value=Path("C:/Users/test")):
            base, media = detect_anki_paths()
            assert base == Path("C:/Users/test/AppData/Roaming/Anki2")
            assert media == Path("C:/Users/test/AppData/Roaming/Anki2/User 1/collection.media")


def test_detect_paths_windows_follows_appdata(monkeypatch):
    """A redirected profile moves %APPDATA%, and Anki's folder with it."""
    monkeypatch.setenv("APPDATA", "D:/Profiles/test/Roaming")
    with patch("platform.system", return_value="Windows"):
        with patch("pathlib.Path.home", return_value=Path("C:/Users/test")):
            base, media = detect_anki_paths()
    assert base == Path("D:/Profiles/test/Roaming/Anki2")
    assert media == Path("D:/Profiles/test/Roaming/Anki2/User 1/collection.media")


def test_detect_paths_linux_standard():
    with patch("platform.system", return_value="Linux"):
        # Mock uname to NOT include microsoft (standard linux)
        uname_mock = MagicMock()
        uname_mock.release.lower.return_value = "5.4.0-generic"
        with patch("platform.uname", return_value=uname_mock):
            with patch("pathlib.Path.home", return_value=Path("/home/test")):
                base, media = detect_anki_paths()
                assert base == Path("/home/test/.local/share/Anki2")
                assert media == Path("/home/test/.local/share/Anki2/User 1/collection.media")


def test_detect_paths_linux_wsl():
    with patch("platform.system", return_value="Linux"):
        # Mock uname to indicate WSL
        uname_mock = MagicMock()
        uname_mock.release.lower.return_value = "4.4.0-microsoft-standard"

        with patch("platform.uname", return_value=uname_mock):
            # Mock filesystem for /mnt/c/Users
            # We need to mock Path.exists and Path.iterdir for /mnt/c/Users

            # Helper to mock iterdir returning a list of mocked paths
            windows_user = MagicMock(spec=Path)
            windows_user.name = "WinUser"
            # The logic relies on user_dir / ... so we need __truediv__ to work partially or just check return
            # But detect_anki_paths constructs the return path from the FOUND windows_user path object.

            # Let's intercept Path construction or just rely on the logic that returns (base, media)
            # The logic is:
            # users_dir = Path("/mnt/c/Users")
            # candidates = [p for p in users_dir.iterdir() if p.is_dir() and p.name not in ...]
            # base = candidates[0] / "AppData/Roaming/Anki2"

            # We need to mock Path.exists and Path.iterdir for /mnt/c/Users

            # Helper to mock iterdir returning a list of mocked paths
            # We must use autospec=True so self is passed correctly to side_effect

            def exists_side_effect(self):
                # Handle Windows path separators if running on Windows
                return str(self).replace("\\", "/") == "/mnt/c/Users"

            with patch("pathlib.Path.exists", autospec=True, side_effect=exists_side_effect):
                with patch("pathlib.Path.iterdir") as mock_iterdir:
                    with patch("pathlib.Path.is_dir", return_value=True):
                        # iterdir needs to return Path-like objects
                        # The code iterates over `users_dir.iterdir()`.

                        # We return a dummy path representing the windows user
                        # It needs to behave like a path (have .name)
                        win_user_path = MagicMock(spec=Path)
                        win_user_path.name = "WinAdam"
                        # When divided, it should return a new path. Ideally we just rely on logic using .name
                        # Logic: win_home = candidates[0] (which is win_user_path)
                        # base = win_home / "AppData/Roaming/Anki2"

                        # We need __truediv__ to work on the mock to return a usable path
                        # Or we can make win_user_path a real Path object that is mocked solely for the iteration?
                        # No, simpler: make it a MagicMock that returns a specific string when converted or divided.

                        # Actually, let's just make it a real Path object but we need to ensure it doesn't try to touch filesystem
                        # safely.
                        # Since we mocked iterdir to return it, and logic is:
                        # base = win_home / "AppData/Roaming/Anki2"
                        # The division on a MagicMock returns a MagicMock.
                        # So base will be a MagicMock.
                        # verify: assert base == ... might fail if comparing MagicMock to Path.

                        # BETTER APPROACH:
                        # Mock iterdir to return a concrete Path object that points to a harmless location
                        # e.g. Path("/mnt/c/Users/WinAdam")
                        # Since we already mocked exists/is_dir globally (or at least specifically for the check),
                        # subsequent checks on this path might occur?
                        # The logic DOES NOT check exists on the result base. It just returns it.

                        win_user_path = Path("/mnt/c/Users/WinAdam")
                        mock_iterdir.return_value = [win_user_path]

                        # Run
                        base, media = detect_anki_paths()

                        # Verify
                        expected_base = Path("/mnt/c/Users/WinAdam/AppData/Roaming/Anki2")
                        assert base == expected_base
                        assert media == expected_base / "User 1/collection.media"


def test_detect_paths_unknown():
    with patch("platform.system", return_value="Haiku"):
        base, media = detect_anki_paths()
        assert base is None
        assert media == Path(".")
