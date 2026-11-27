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

import contextlib
import inspect
import json
import os
import re
import tempfile
import threading
import time

import aqt
from anki.cards import Card
from anki.errors import NotFoundError
from anki.notes import Note
from aqt import gui_hooks
from aqt.qt import QMessageBox, QTimer


def _parse_anki_version(version_str: str) -> tuple[int, ...]:
    parts: list[int] = []
    for segment in version_str.split("."):
        match = re.match(r"^(\d+)", segment)
        if match is None:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


anki_version = _parse_anki_version(aqt.appVersion)

if anki_version < (26, 8, 1):
    raise Exception("Minimum Anki version supported: 26.8.1")

from . import util, web  # noqa: E402
from .actions_arete import AreteActions  # noqa: E402
from .actions_cards import CardActions  # noqa: E402
from .actions_decks import DeckActions  # noqa: E402
from .actions_gui import GuiActions  # noqa: E402
from .actions_media import MediaActions  # noqa: E402
from .actions_models import ModelActions  # noqa: E402
from .actions_notes import NoteActions  # noqa: E402
from .edit import Edit  # noqa: E402
from .web import format_exception_reply, format_success_reply  # noqa: E402

# A burst of write actions gets one UI refresh, this long after its last write.
REFRESH_DELAY_MS = 250
# While writes keep coming, a pending refresh still runs at least this often.
REFRESH_MAX_WAIT_S = 2.0


#
# AnkiConnect
#


class AnkiConnect(
    AreteActions,
    DeckActions,
    NoteActions,
    CardActions,
    ModelActions,
    MediaActions,
    GuiActions,
):
    def __init__(self):
        self.log = None
        self.timer = None
        self.refreshTimer = None
        self.refreshPendingSince = 0.0
        self.refreshing = False
        self._permissionPromptOpen = False
        self.actions = self.buildActionTable()
        self.server = web.WebServer(self.handler)

    def buildActionTable(self):
        """Map each action name to its method, once, for every request to use.

        An action answers to its method name, whatever version the request names.
        """
        table = {}
        for methodName, methodInst in inspect.getmembers(self, predicate=inspect.ismethod):
            if getattr(methodInst, "api", False):
                table[methodName] = methodInst
        return table

    def initLogging(self):
        if self.log is not None:
            return
        logPath = util.setting("apiLogPath")
        if logPath is not None:
            self.log = open(logPath, "w", encoding="utf-8")

    def startWebServer(self):
        port = None
        try:
            port = util.setting("webBindPort")
            self.server.listen()

            # parent to main window and keep reference to prevent garbage collection
            self.timer = QTimer(self.window())
            self.timer.timeout.connect(self.advance)
            self.timer.start(util.setting("apiPollInterval"))
        except OSError as e:
            QMessageBox.critical(
                self.window(),
                "AnkiConnect",
                f"Failed to listen on port {port}.\n"
                f"Make sure it is available and is not in use.\n\n{e}",
            )

    def ensureRunning(self):
        self.initLogging()
        if self.server.sock is None:
            self.startWebServer()

    def shutdown(self):
        if self.timer is not None:
            self.timer.stop()
            self.timer = None
        if self.refreshTimer is not None:
            self.refreshTimer.stop()
            self.refreshTimer = None
        self.server.close()
        if self.log is not None:
            with contextlib.suppress(OSError):
                self.log.close()
            self.log = None

    def logEvent(self, name, data):
        if self.log is not None:
            self.log.write(f"[{name}]\n")
            json.dump(data, self.log, indent=4, sort_keys=True)
            self.log.write("\n\n")
            self.log.flush()

    def advance(self):
        self.server.advance()

    def handler(self, request):
        self.logEvent("request", request)

        name = request.get("action", "")
        version = request.get("version", 4)
        params = request.get("params", {})
        key = request.get("key")

        try:
            if key != util.setting("apiKey") and name != "requestPermission":
                raise Exception("valid api key must be provided")

            method = self.actions.get(name) if isinstance(name, str) else None

            if method is None:
                raise Exception("unsupported action")

            api_return_value = method(**params)
            reply = format_success_reply(version, api_return_value)

        except Exception as e:
            reply = format_exception_reply(version, e)

        self.logEvent("reply", reply)
        return reply

    def window(self):
        return aqt.mw

    def reviewer(self):
        reviewer = self.window().reviewer
        if reviewer is None:
            raise Exception("reviewer is not available")
        return reviewer

    def collection(self):
        collection = self.window().col
        if collection is None:
            raise Exception("collection is not available")
        return collection

    def decks(self):
        decks = self.collection().decks
        if decks is None:
            raise Exception("decks are not available")
        return decks

    def scheduler(self):
        scheduler = self.collection().sched
        if scheduler is None:
            raise Exception("scheduler is not available")
        return scheduler

    def database(self):
        database = self.collection().db
        if database is None:
            raise Exception("database is not available")
        return database

    def media(self):
        media = self.collection().media
        if media is None:
            raise Exception("media is not available")
        return media

    def getModel(self, modelName):
        model = self.collection().models.by_name(modelName)
        if model is None:
            raise Exception(f"model was not found: {modelName}")
        return model

    def getField(self, model, fieldName):
        fieldMap = self.collection().models.field_map(model)
        if fieldName not in fieldMap:
            raise Exception(f"field was not found in {model['name']}: {fieldName}")
        return fieldMap[fieldName][1]

    def getTemplate(self, model, templateName):
        for ankiTemplate in model["tmpls"]:
            if ankiTemplate["name"] == templateName:
                return ankiTemplate
        raise Exception(f"template was not found in {model['name']}: {templateName}")

    def scheduleRefresh(self):
        """Refresh Anki's UI once after a burst of writes, instead of once per write.

        Write actions change the collection directly, which no Anki screen hears about,
        so mw.reset() tells them all (deck list, overview, reviewer, Browser, undo
        menu). It runs REFRESH_DELAY_MS after the last write, from its own single-shot
        timer on the main thread: requests that arrive meanwhile restart the timer, a
        pending refresh is never held back longer than REFRESH_MAX_WAIT_S, and nothing
        modal that a refresh opens can block the request timer.
        """
        if threading.current_thread() is not threading.main_thread():
            self.window().taskman.run_on_main(self.scheduleRefresh)
            return
        if self.refreshTimer is None:
            self.refreshTimer = QTimer(self.window())
            self.refreshTimer.setSingleShot(True)
            self.refreshTimer.timeout.connect(self.refreshUi)
        now = time.monotonic()
        if not self.refreshTimer.isActive():
            self.refreshPendingSince = now
        elif now - self.refreshPendingSince >= REFRESH_MAX_WAIT_S:
            return
        self.refreshTimer.start(REFRESH_DELAY_MS)

    def refreshUi(self):
        if self.refreshing:
            # A refresh is still on the stack (inside a dialog it opened); try later.
            self.scheduleRefresh()
            return
        self.refreshing = True
        try:
            self.window().reset()
        finally:
            self.refreshing = False

    def raiseNotFoundError(self, errorMsg):
        raise NotFoundError(errorMsg, None, None, None)

    def getCard(self, card_id: int) -> Card:
        try:
            return self.collection().get_card(card_id)
        except NotFoundError:
            self.raiseNotFoundError(f"Card was not found: {card_id}")

    def getNote(self, note_id: int) -> Note:
        try:
            return self.collection().get_note(note_id)
        except NotFoundError:
            self.raiseNotFoundError(f"Note was not found: {note_id}")


#
# Entry
#

# when run inside Anki, `__name__` would be either numeric,
# or, if installed via `link.sh`, `AnkiConnectDev`
if __name__ != "__main__":
    try:
        Edit.register_with_anki()

        ac = AnkiConnect()
        ac.initLogging()
        ac.startWebServer()
        gui_hooks.profile_will_close.append(ac.shutdown)
        gui_hooks.profile_did_open.append(ac.ensureRunning)

        # Initialize Arete Hooks
        from . import arete

        arete.setup_reviewer_shortcut()
        gui_hooks.browser_will_show_context_menu.append(arete.on_browser_context_menu)
    except Exception as e:
        import traceback

        log_path = os.path.join(tempfile.gettempdir(), "arete_ankiconnect_startup_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"Add-on failed to start: {e}\n")
            f.write(traceback.format_exc())
        # Re-raise so Anki still knows something is wrong, but now we have a log.
        raise
