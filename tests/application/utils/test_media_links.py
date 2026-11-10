"""Wikilinks become obsidian:// links; an unreadable attachment folder does not stop the index."""

import logging
import os

import pytest

from arete.application.sync.converter import markdown_to_anki_html
from arete.application.utils.media import build_filename_index, transform_wikilinks_to_uri

URI = "obsidian://open?vault=My%20Vault&file="


def test_a_wikilink_becomes_a_link_to_the_note_in_this_vault():
    out = transform_wikilinks_to_uri("See [[Some Note]] now", "My Vault")
    assert out == f"See [Some Note]({URI}Some%20Note) now"


def test_a_piped_wikilink_shows_the_display_text_and_targets_the_note():
    out = transform_wikilinks_to_uri("[[Some Note|the note]]", "My Vault")
    assert out == f"[the note]({URI}Some%20Note)"


def test_whitespace_around_target_and_display_is_trimmed():
    assert transform_wikilinks_to_uri("[[ A | b ]]", "My Vault") == f"[b]({URI}A)"


def test_folder_separators_stay_readable_in_the_target():
    out = transform_wikilinks_to_uri("[[Folder/Note]]", "My Vault")
    assert out == f"[Folder/Note]({URI}Folder/Note)"


def test_an_image_embed_is_not_taken_for_a_note_link():
    out = transform_wikilinks_to_uri("![[pic.png]] and [[A]]", "My Vault")
    assert out == f"![[pic.png]] and [A]({URI}A)"


def test_the_link_survives_markdown_rendering_as_an_anchor():
    html = markdown_to_anki_html(transform_wikilinks_to_uri("See [[Some Note]]", "My Vault"))
    assert html == (
        '<p>See <a href="obsidian://open?vault=My%20Vault&amp;file=Some%20Note">Some Note</a></p>'
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_an_unreadable_attachment_subfolder_is_skipped_not_fatal(tmp_path):
    attachments = tmp_path / "attachments"
    (attachments / "open").mkdir(parents=True)
    (attachments / "locked").mkdir()
    (attachments / "open" / "a.png").write_bytes(b"a")
    (attachments / "locked" / "b.png").write_bytes(b"b")
    (attachments / "locked").chmod(0o000)
    try:
        index = build_filename_index(tmp_path, logging.getLogger("test_media_links"))
    finally:
        (attachments / "locked").chmod(0o755)
    assert list(index) == ["a.png"]
