# Copyright 2016-2021 Alex Yatskov
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import copy
import enum
import os
import sys

import anki
import anki.sync
import aqt

#
# Utilities
#


class MediaType(enum.Enum):
    Audio = 1
    Video = 2
    Picture = 3


def download(url):
    client = anki.sync.AnkiRequestsClient()
    client.timeout = setting("webTimeout") / 1000

    resp = client.get(url)
    if resp.status_code != 200:
        raise Exception(f"{url} download failed with return code {resp.status_code}")

    return client.streamContent(resp)


def api(*versions):
    def decorator(func):
        func.versions = versions
        func.api = True
        return func

    return decorator


def cardQuestion(card):
    if getattr(card, "question", None) is None:
        return card._getQA()["q"]

    return card.question()


def cardAnswer(card):
    if getattr(card, "answer", None) is None:
        return card._getQA()["a"]

    return card.answer()


DEFAULT_CONFIG = {
    "apiKey": None,
    "apiLogPath": None,
    "apiPollInterval": 25,
    "apiVersion": 6,
    # Higher backlog helps absorb bursty parallel clients during sync.
    "webBacklog": 128,
    "webBindAddress": os.getenv("ANKICONNECT_BIND_ADDRESS", "127.0.0.1"),
    "webBindPort": 8765,
    "webCorsOrigin": os.getenv("ANKICONNECT_CORS_ORIGIN", None),
    "webCorsOriginList": ["http://localhost"],
    "ignoreOriginList": [],
    "webTimeout": 10000,
}


def setting(key):
    # Support environment variable overrides for critical settings in Docker/CI
    if key == "webBindAddress":
        env_val = os.getenv("ANKICONNECT_BIND_ADDRESS")
        if env_val:
            return env_val
    if key == "webBindPort":
        env_val = os.getenv("ANKICONNECT_BIND_PORT")
        if env_val:
            return int(env_val)
    if key == "apiKey":
        env_val = os.getenv("ANKICONNECT_API_KEY")
        if env_val:
            return env_val

    try:
        value = config().get(key, DEFAULT_CONFIG[key])
    except:
        raise Exception(f"setting {key} not found")
    # A caller may mutate what it gets (CORS list, permission prompts); keep the cache intact.
    return copy.deepcopy(value)


_configFiles = None
_configCache = None


def _fileStamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def config():
    """The add-on config exactly as `addonManager.getConfig` returns it.

    getConfig opens and parses config.json and meta.json on every call. They are re-read
    here only when either file's stamp changes, so an edit in Anki's config dialog (which
    writes meta.json) or by hand still applies to the next request.
    """
    global _configFiles, _configCache
    manager = aqt.mw.addonManager
    if _configFiles is None:
        folder = manager.addonsFolder(manager.addonFromModule(__name__))
        _configFiles = (os.path.join(folder, "config.json"), os.path.join(folder, "meta.json"))
    stamp = tuple(_fileStamp(path) for path in _configFiles)
    if _configCache is None or _configCache[0] != stamp:
        _configCache = (stamp, manager.getConfig(__name__))
    return _configCache[1]


# see https://github.com/FooSoft/anki-connect/issues/308
# fixed in https://github.com/ankitects/anki/commit/0b2a226d
def patch_anki_2_1_50_having_null_stdout_on_windows():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf8")
