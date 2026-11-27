"""Media storage, retrieval, listing, deletion, and note media attachment actions."""

import base64
import glob
import hashlib
import os
import tempfile
import unicodedata
from pathlib import Path

from . import util


class MediaActions:
    """Media folder actions with path-traversal protection."""

    def _validateMediaFilename(self, filename):
        if (
            not isinstance(filename, str)
            or not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or os.path.basename(filename) != filename
        ):
            raise Exception(f"invalid media filename: {filename!r}")
        return filename

    def _validateMediaSourcePath(self, path):
        if not isinstance(path, str) or not path:
            raise Exception("invalid media path")
        raw = Path(path)
        if ".." in raw.parts:
            raise Exception("media path must not contain path traversal segments")
        resolved = raw.resolve()
        allowed_roots = [
            Path(self.media().dir()).resolve(),
            Path(tempfile.gettempdir()).resolve(),
        ]
        try:
            vault_root = util.setting("vault_root")
        except Exception:
            vault_root = None
        if vault_root:
            allowed_roots.append(Path(vault_root).expanduser().resolve())
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise Exception("media path is outside allowed directories")
        if not resolved.is_file():
            raise Exception(f"media file not found: {path}")
        return resolved

    def addMediaFromNote(self, ankiNote, note):
        self.addMedia(ankiNote, note.get("audio"), util.MediaType.Audio)
        self.addMedia(ankiNote, note.get("video"), util.MediaType.Video)
        self.addMedia(ankiNote, note.get("picture"), util.MediaType.Picture)

    def _attachMediaItem(self, ankiNote, media, mediaType):
        try:
            mediaFilename = self.storeMediaFile(
                media["filename"],
                data=media.get("data"),
                path=media.get("path"),
                url=media.get("url"),
                skipHash=media.get("skipHash"),
                deleteExisting=media.get("deleteExisting"),
            )
            if mediaFilename is None:
                return
            if mediaType is util.MediaType.Picture:
                tag = f'<img src="{mediaFilename}">'
            elif mediaType in (util.MediaType.Audio, util.MediaType.Video):
                tag = f"[sound:{mediaFilename}]"
            else:
                return
            for field in media["fields"]:
                if field in ankiNote:
                    ankiNote[field] += tag
        except Exception as e:
            errorMessage = str(e).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            for field in media["fields"]:
                if field in ankiNote:
                    ankiNote[field] += errorMessage

    def addMedia(self, ankiNote, mediaObjectOrList, mediaType):
        if mediaObjectOrList is None:
            return

        mediaList = (
            mediaObjectOrList if isinstance(mediaObjectOrList, list) else [mediaObjectOrList]
        )

        for media in mediaList:
            if media is not None and len(media["fields"]) > 0:
                self._attachMediaItem(ankiNote, media, mediaType)

    @util.api()
    def storeMediaFile(
        self, filename, data=None, path=None, url=None, skipHash=None, deleteExisting=True
    ):
        if not (data or path or url):
            raise Exception('You must provide a "data", "path", or "url" field.')
        safe_filename = self._validateMediaFilename(filename)
        if data:
            mediaData = base64.b64decode(data)
        elif path:
            resolved = self._validateMediaSourcePath(path)
            with open(resolved, "rb") as f:
                mediaData = f.read()
        elif url:
            mediaData = util.download(url)

        if skipHash is None:
            skip = False
        else:
            m = hashlib.md5()
            m.update(mediaData)
            skip = skipHash == m.hexdigest()

        if skip:
            return None
        if deleteExisting:
            self.deleteMediaFile(safe_filename)
        return self.media().write_data(safe_filename, mediaData)

    @util.api()
    def retrieveMediaFile(self, filename):
        if not isinstance(filename, str):
            return False
        filename = os.path.basename(filename)
        filename = unicodedata.normalize("NFC", filename)
        filename = self.media().stripIllegal(filename)
        if filename in {"", ".", ".."}:
            return False

        path = os.path.join(self.media().dir(), filename)
        if os.path.isfile(path):
            with open(path, "rb") as file:
                return base64.b64encode(file.read()).decode("ascii")

        return False

    @util.api()
    def getMediaFilesNames(self, pattern="*"):
        if (
            not isinstance(pattern, str)
            or pattern in {"", ".", ".."}
            or os.path.isabs(pattern)
            or "/" in pattern
            or "\\" in pattern
        ):
            raise Exception(f"invalid media pattern: {pattern!r}")
        media_dir = Path(self.media().dir()).resolve()
        search_path = os.path.join(str(media_dir), pattern)
        return [
            os.path.basename(p)
            for p in glob.glob(search_path)
            if Path(p).resolve().is_relative_to(media_dir)
        ]

    @util.api()
    def deleteMediaFile(self, filename):
        safe_filename = self._validateMediaFilename(filename)
        self.media().trash_files([safe_filename])

    @util.api()
    def getMediaDirPath(self):
        return os.path.abspath(self.media().dir())
