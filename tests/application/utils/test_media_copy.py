"""An image already in Anki's media folder, byte for byte, is not copied again."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from arete.application.utils import media
from arete.application.utils.media import _copy_to_anki_media

LOG = logging.getLogger("test-media-copy")


@pytest.fixture
def src(tmp_path: Path) -> Path:
    p = tmp_path / "vault" / "pic.png"
    p.parent.mkdir()
    p.write_bytes(b"\x89PNG one")
    return p


def test_an_identical_file_is_not_copied_again(tmp_path: Path, src: Path):
    dest = tmp_path / "media"
    assert _copy_to_anki_media(src, dest, LOG) == "pic.png"
    with patch.object(media.shutil, "copy2") as copy2:
        assert _copy_to_anki_media(src, dest, LOG) == "pic.png"
    copy2.assert_not_called()
    assert (dest / "pic.png").read_bytes() == b"\x89PNG one"


def test_a_different_file_of_the_same_name_still_gets_its_own_copy(tmp_path: Path, src: Path):
    dest = tmp_path / "media"
    dest.mkdir()
    (dest / "pic.png").write_bytes(b"\x89PNG two")
    name = _copy_to_anki_media(src, dest, LOG)
    assert name is not None and name.startswith("pic_") and name.endswith(".png")
    assert (dest / name).read_bytes() == b"\x89PNG one"
    assert (dest / "pic.png").read_bytes() == b"\x89PNG two"


def test_a_same_size_different_file_is_not_mistaken_for_a_copy(tmp_path: Path, src: Path):
    dest = tmp_path / "media"
    dest.mkdir()
    (dest / "pic.png").write_bytes(b"\x89PNG ONE")
    name = _copy_to_anki_media(src, dest, LOG)
    assert name != "pic.png"
    assert (dest / "pic.png").read_bytes() == b"\x89PNG ONE"


def test_a_source_inside_the_media_folder_resolves_as_before(tmp_path: Path):
    """copy2 refuses to copy a file onto itself; that embed fails as it always has."""
    dest = tmp_path / "media"
    dest.mkdir()
    own = dest / "pic.png"
    own.write_bytes(b"\x89PNG")
    assert _copy_to_anki_media(own, dest, LOG) is None
