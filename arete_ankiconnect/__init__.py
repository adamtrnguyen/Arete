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

import logging
import re

import aqt


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

import base64
import glob
import hashlib
import inspect
import json
import os
import os.path
import tempfile
import threading
import time
import unicodedata
from pathlib import Path


import anki
import anki.exporting
import anki.storage
from anki.cards import Card
from anki.consts import MODEL_CLOZE
from anki.errors import NotFoundError
from anki.exporting import AnkiPackageExporter
from anki.importing import AnkiPackageImporter
from anki.notes import Note
from anki.utils import field_checksum, int_time
from aqt import gui_hooks, mw
from aqt.browser import Browser
from aqt.qt import QAction, QCheckBox, QKeySequence, QMenu, QMessageBox, Qt, QTimer
from aqt.reviewer import Reviewer
from aqt.utils import showWarning, tooltip

from . import util, web
from .edit import Edit
from .web import format_exception_reply, format_success_reply

# A burst of write actions gets one UI refresh, this long after its last write.
REFRESH_DELAY_MS = 250
# While writes keep coming, a pending refresh still runs at least this often.
REFRESH_MAX_WAIT_S = 2.0




#
# AnkiConnect
#


class AnkiConnect:
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
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None

    def save_model(self, models, ankiModel):
        models.update_dict(ankiModel)

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
            raise Exception("field was not found in {}: {}".format(model["name"], fieldName))
        return fieldMap[fieldName][1]

    def getTemplate(self, model, templateName):
        for ankiTemplate in model["tmpls"]:
            if ankiTemplate["name"] == templateName:
                return ankiTemplate
        raise Exception("template was not found in {}: {}".format(model["name"], templateName))

    def startEditing(self):
        self.scheduleRefresh()

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

    def stopEditing(self):
        if self.collection() is not None:
            self.window().maybeReset()

    def createNote(self, note):
        collection = self.collection()

        model = collection.models.by_name(note["modelName"])
        if model is None:
            raise Exception("model was not found: {}".format(note["modelName"]))

        deck = collection.decks.by_name(note["deckName"])
        if deck is None:
            raise Exception("deck was not found: {}".format(note["deckName"]))

        ankiNote = anki.notes.Note(collection, model)
        ankiNote.note_type()["did"] = deck["id"]
        if "tags" in note:
            ankiNote.tags = note["tags"]

        for name, value in note["fields"].items():
            for ankiName in ankiNote.keys():
                if name.lower() == ankiName.lower():
                    ankiNote[ankiName] = value
                    break

        self.addMediaFromNote(ankiNote, note)

        allowDuplicate = False
        duplicateScope = None
        duplicateScopeDeckName = None
        duplicateScopeCheckChildren = False
        duplicateScopeCheckAllModels = False

        if "options" in note:
            options = note["options"]
            if "allowDuplicate" in options:
                allowDuplicate = options["allowDuplicate"]
                if type(allowDuplicate) is not bool:
                    raise Exception('option parameter "allowDuplicate" must be boolean')
            if "duplicateScope" in options:
                duplicateScope = options["duplicateScope"]
            if "duplicateScopeOptions" in options:
                duplicateScopeOptions = options["duplicateScopeOptions"]
                if "deckName" in duplicateScopeOptions:
                    duplicateScopeDeckName = duplicateScopeOptions["deckName"]
                if "checkChildren" in duplicateScopeOptions:
                    duplicateScopeCheckChildren = duplicateScopeOptions["checkChildren"]
                    if type(duplicateScopeCheckChildren) is not bool:
                        raise Exception(
                            'option parameter "duplicateScopeOptions.checkChildren" must be boolean'
                        )
                if "checkAllModels" in duplicateScopeOptions:
                    duplicateScopeCheckAllModels = duplicateScopeOptions["checkAllModels"]
                    if type(duplicateScopeCheckAllModels) is not bool:
                        raise Exception(
                            'option parameter "duplicateScopeOptions.checkAllModels" must be boolean'
                        )

        duplicateOrEmpty = self.isNoteDuplicateOrEmptyInScope(
            ankiNote,
            deck,
            collection,
            duplicateScope,
            duplicateScopeDeckName,
            duplicateScopeCheckChildren,
            duplicateScopeCheckAllModels,
        )

        if duplicateOrEmpty == 1:
            raise Exception("cannot create note because it is empty")
        elif duplicateOrEmpty == 2:
            if allowDuplicate:
                return ankiNote
            raise Exception("cannot create note because it is a duplicate")
        elif duplicateOrEmpty == 0:
            return ankiNote
        else:
            raise Exception("cannot create note for unknown reason")

    def isNoteDuplicateOrEmptyInScope(
        self,
        note,
        deck,
        collection,
        duplicateScope,
        duplicateScopeDeckName,
        duplicateScopeCheckChildren,
        duplicateScopeCheckAllModels,
    ):
        # Returns: 1 if first is empty, 2 if first is a duplicate, 0 otherwise.

        # note.fields_check returns if a note is a global duplicate with the specific model.
        # This is used as the default check, and the rest of this function is manually
        # checking if the note is a duplicate with additional options.
        if duplicateScope != "deck" and not duplicateScopeCheckAllModels:
            return note.fields_check() or 0

        # Primary field for uniqueness
        val = note.fields[0]
        if not val.strip():
            return 1
        csum = field_checksum(val)

        # Create dictionary of deck ids
        dids = None
        if duplicateScope == "deck":
            did = deck["id"]
            if duplicateScopeDeckName is not None:
                deck2 = collection.decks.by_name(duplicateScopeDeckName)
                if deck2 is None:
                    # Invalid deck, so cannot be duplicate
                    return 0
                did = deck2["id"]

            dids = {did: True}
            if duplicateScopeCheckChildren:
                for kv in collection.decks.children(did):
                    dids[kv[1]] = True

        # Build query
        query = "select id from notes where csum=?"
        queryArgs = [csum]
        if note.id:
            query += " and id!=?"
            queryArgs.append(note.id)
        if not duplicateScopeCheckAllModels:
            query += " and mid=?"
            queryArgs.append(note.mid)

        # Search
        for noteId in note.col.db.list(query, *queryArgs):
            if dids is None:
                # Duplicate note exists in the collection
                return 2
            # Validate that a card exists in one of the specified decks
            for cardDeckId in note.col.db.list("select did from cards where nid=?", noteId):
                if cardDeckId in dids:
                    return 2

        # Not a duplicate
        return 0

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

    def deckStatsToJson(self, due_tree):
        deckStats = {
            "deck_id": due_tree.deck_id,
            "name": due_tree.name,
            "new_count": due_tree.new_count,
            "learn_count": due_tree.learn_count,
            "review_count": due_tree.review_count,
            "total_in_deck": due_tree.total_in_deck,
        }
        return deckStats

    def collectDeckTreeChildren(self, parent_node):
        allNodes = {parent_node.deck_id: parent_node}
        for child in parent_node.children:
            for deckId, childNode in self.collectDeckTreeChildren(child).items():
                allNodes[deckId] = childNode
        return allNodes

    #
    # Miscellaneous
    #

    @util.api()
    def version(self):
        return util.setting("apiVersion")

    @util.api()
    def requestPermission(self, origin, allowed):
        results = {
            "permission": "denied",
        }
        origin = origin.replace("\r", "").replace("\n", "") if isinstance(origin, str) else ""

        if allowed:
            results = {
                "permission": "granted",
                "requireApikey": bool(util.setting("apiKey")),
                "version": util.setting("apiVersion"),
            }

        elif not origin or origin in util.setting("ignoreOriginList") or self._permissionPromptOpen:
            pass  # defaults to denied

        else:  # prompt the user
            self._permissionPromptOpen = True
            try:
                msg = QMessageBox(None)
                msg.setWindowTitle("A website wants to access to Anki")
                msg.setText(
                    f'"{origin}" requests permission to use Anki through AnkiConnect. Do you want to give it access?'
                )
                msg.setInformativeText(
                    "By granting permission, you'll allow the website to modify your collection on your behalf, including the execution of destructive actions such as deck deletion."
                )
                msg.setWindowIcon(self.window().windowIcon())
                msg.setIcon(QMessageBox.Icon.Question)
                msg.setStandardButtons(
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                msg.setDefaultButton(QMessageBox.StandardButton.No)
                msg.setCheckBox(
                    QCheckBox(text=f'Ignore further requests from "{origin}"', parent=msg)
                )
                msg.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
                pressedButton = msg.exec()

                if pressedButton == QMessageBox.StandardButton.Yes:
                    config = aqt.mw.addonManager.getConfig(__name__) or {}
                    cors_list = util.setting("webCorsOriginList")
                    if origin not in cors_list:
                        cors_list.append(origin)
                    config["webCorsOriginList"] = cors_list
                    aqt.mw.addonManager.writeConfig(__name__, config)
                    results = {
                        "permission": "granted",
                        "requireApikey": bool(util.setting("apiKey")),
                        "version": util.setting("apiVersion"),
                    }

                elif (
                    origin
                    and pressedButton == QMessageBox.StandardButton.No
                    and msg.checkBox().isChecked()
                ):
                    config = aqt.mw.addonManager.getConfig(__name__) or {}
                    ignore_list = util.setting("ignoreOriginList")
                    if origin not in ignore_list:
                        ignore_list.append(origin)
                    config["ignoreOriginList"] = ignore_list
                    aqt.mw.addonManager.writeConfig(__name__, config)
            finally:
                self._permissionPromptOpen = False

        return results

    @util.api()
    def getProfiles(self):
        return self.window().pm.profiles()

    @util.api()
    def loadProfile(self, name):
        if name not in self.window().pm.profiles():
            return False

        if self.window().isVisible():
            cur_profile = self.window().pm.name
            if cur_profile != name:
                self.window().unloadProfileAndShowProfileManager()

                def waiter():
                    # This function waits until main window is closed
                    # It's needed cause sync can take quite some time
                    # And if we call loadProfile until sync is ended things will go wrong
                    if self.window().isVisible():
                        QTimer.singleShot(1000, waiter)
                    else:
                        self.loadProfile(name)

                waiter()
        else:
            self.window().pm.load(name)
            self.window().loadProfile()
            self.window().profileDiag.closeWithoutQuitting()

        return True

    @util.api()
    def sync(self):
        self.window().onSync()

    @util.api()
    def multi(self, actions):
        return list(map(self.handler, actions))

    @util.api()
    def getNumCardsReviewedToday(self):
        return self.database().scalar(
            "select count() from revlog where id > ?", (self.scheduler().day_cutoff - 86400) * 1000
        )

    @util.api()
    def getNumCardsReviewedByDay(self):
        return self.database().all(
            'select date(id/1000 - ?, "unixepoch", "localtime") as day, count() from revlog group by day order by day desc',
            int(time.strftime("%H", time.localtime(self.scheduler().day_cutoff))) * 3600,
        )

    @util.api()
    def getCollectionStatsHTML(self, wholeCollection=True):
        stats = self.collection().stats()
        stats.wholeCollection = wholeCollection
        return stats.report()

    #
    # Decks
    #

    @util.api()
    def deckNames(self):
        # What decks.allNames() returns, without its two deprecation warnings.
        names = self.decks().all_names_and_ids(skip_empty_default=False, include_filtered=True)
        return [name.name for name in names]

    @util.api()
    def deckNamesAndIds(self):
        decks = {}
        for deck in self.deckNames():
            decks[deck] = self.decks().id(deck)

        return decks

    @util.api()
    def getDecks(self, cards):
        decks = {}
        for card in cards:
            did = self.database().scalar("select did from cards where id=?", card)
            if did is None:
                continue
            deck_obj = self.decks().get(did, default=False)
            if deck_obj is None:
                continue
            deck = deck_obj["name"]
            if deck in decks:
                decks[deck].append(card)
            else:
                decks[deck] = [card]

        return decks

    @util.api()
    def createDeck(self, deck):
        try:
            self.startEditing()
            did = self.decks().id(deck)
        finally:
            self.stopEditing()

        return did

    @util.api()
    def changeDeck(self, cards, deck):
        self.startEditing()

        did = self.collection().decks.id(deck)
        mod = int_time()
        usn = self.collection().usn()

        # normal cards
        scids = anki.utils.ids2str(cards)
        # remove any cards from filtered deck first
        self.collection().sched.remFromDyn(cards)

        # then move into new deck
        self.collection().db.execute(
            "update cards set usn=?, mod=?, did=? where id in " + scids, usn, mod, did
        )
        self.stopEditing()

    @util.api()
    def deleteDecks(self, decks, cardsToo=False):
        if not cardsToo:
            # Anki deletes a deck's cards with the deck, and its delete call takes no flag
            # to keep them, so a request to keep them is refused rather than ignored.
            raise Exception(
                "Since Anki 2.1.28 it's not possible to delete decks without deleting cards as well"
            )
        try:
            self.startEditing()
            decks = filter(lambda d: d in self.deckNames(), decks)
            for deck in decks:
                did = self.decks().id(deck)
                # decks.rem() is a deprecated wrapper that ignores cardsToo and calls this.
                self.decks().remove([did])
        finally:
            self.stopEditing()

    @util.api()
    def createFilteredDeck(self, name, cids, reschedule=True):
        """Create/rebuild a filtered deck with cards in the specified CID order."""
        if not isinstance(cids, list) or not cids:
            raise Exception("No card IDs provided")
        for cid in cids:
            if type(cid) is not int:
                raise Exception(f"invalid card id: {cid!r}")

        logger = logging.getLogger("arete_ankiconnect")
        self.startEditing()
        try:
            col = self.collection()

            # 1. Find or create filtered deck
            did = col.decks.id(name, create=False)
            if did:
                deck = col.decks.get(did)
                if not deck or not deck.get("dyn"):
                    raise Exception(f"Deck '{name}' exists and is not a filtered deck")
                # Empty existing filtered deck before rebuilding
                col.sched.empty_filtered_deck(did)
            else:
                did = col.decks.new_filtered(name)

            # 2. Configure search terms
            deck = col.decks.get(did)
            if deck is None:
                raise Exception(f"Deck '{name}' could not be loaded")
            query = " OR ".join([f"cid:{cid}" for cid in cids])
            deck["terms"] = [[query, len(cids), 0]]  # [query, limit, order=oldest]
            deck["resched"] = reschedule
            col.decks.save(deck)

            # 3. Rebuild (pull cards into filtered deck)
            col.sched.rebuild_filtered_deck(did)

            # 4. Enforce topological order via due values
            # One update for all cards. A cid listed twice keeps its last position, as
            # it does when each position is saved in turn. Intraday learning cards
            # (queue == 1) store a Unix epoch timestamp in `due`, so leave them untouched.
            pulled = {}
            for i, cid in enumerate(cids):
                try:
                    card = col.get_card(cid)
                except NotFoundError:
                    continue  # Card may not exist
                if card.did == did and card.queue != 1:
                    card.due = i + 1000
                    pulled[card.id] = card
            if pulled:
                try:
                    col.update_cards(list(pulled.values()), skip_undo_entry=True)
                except Exception as e:
                    logger.warning("createFilteredDeck batch update_cards failed: %s", e)
                    # Save one at a time so one bad card cannot block the rest.
                    for card in pulled.values():
                        try:
                            col.update_card(card, skip_undo_entry=True)
                        except Exception as item_err:
                            logger.warning(
                                "createFilteredDeck update_card(%s) failed: %s",
                                card.id,
                                item_err,
                            )

            return did
        finally:
            self.stopEditing()

    @util.api()
    def getHomeDecks(self, cards):
        """Per card id, in order: the deck it belongs to and the filtered deck it sits in.

        `{"homeDeck": name, "filteredDeck": name or None}`; a card queued in a filtered
        deck belongs to the deck it returns to (`odid`). A deck that no longer exists has
        the name None. A card the collection does not hold gets None. One query.
        """
        ids = [cid for cid in cards if type(cid) is int]
        rows = {}
        if ids:
            for cid, did, odid in self.database().all(
                "select id, did, odid from cards where id in %s" % anki.utils.ids2str(ids)
            ):
                rows[cid] = (did, odid)
        names = {}

        def name(did):
            if did not in names:
                deck = self.decks().get(did, default=False)
                names[did] = deck["name"] if deck else None
            return names[did]

        result = []
        for cid in cards:
            if type(cid) is not int or cid not in rows:
                result.append(None)
                continue
            did, odid = rows[cid]
            if odid:
                result.append({"homeDeck": name(odid), "filteredDeck": name(did)})
            else:
                result.append({"homeDeck": name(did), "filteredDeck": None})
        return result

    @util.api()
    def setHomeDeck(self, cards, deck):
        """File the cards under an existing deck without taking any out of a filtered deck.

        A card queued in a filtered deck gets the deck as the one it returns to (`odid`)
        and stays queued; any other card moves to it (`did`). Due dates, queues and the
        rest of the scheduling are untouched.
        """
        col = self.collection()
        did = col.decks.id(deck, create=False)
        deck_dict = col.decks.get(did, default=False) if did else None
        if not did or deck_dict is None:
            raise Exception("deck was not found: {}".format(deck))
        if deck_dict.get("dyn"):
            raise Exception(f"cannot set a filtered deck as home deck: {deck}")

        queued: dict[int, Card] = {}
        loose: list[int] = []
        seen_loose: set[int] = set()
        for cid in cards:
            if type(cid) is not int:
                raise Exception(f"invalid card id: {cid!r}")
            card = self.getCard(cid)
            if card.odid:
                if card.odid != did and card.id not in queued:
                    card.odid = did
                    queued[card.id] = card
            elif card.did != did and card.id not in seen_loose:
                seen_loose.add(card.id)
                loose.append(card.id)

        if queued:
            col.update_cards(list(queued.values()), skip_undo_entry=True)
        if loose:
            col.set_deck(loose, int(did))
        if queued or loose:
            self.scheduleRefresh()

    @util.api()
    def getModelInfo(self, models):
        """Per note type name: canonical name, kind (0 standard, 1 cloze), fields, template count.

        A name the collection does not hold gets None.
        """
        result = {}
        for modelName in models:
            model = self.collection().models.by_name(modelName)
            if model is None:
                result[modelName] = None
                continue
            result[modelName] = {
                "name": model["name"],
                "type": model["type"],
                "fields": [field["name"] for field in model["flds"]],
                "templates": len(model["tmpls"]),
            }
        return result

    @util.api()
    def getDeckConfig(self, deck):
        if deck not in self.deckNames():
            return False

        collection = self.collection()
        did = collection.decks.id(deck)
        return collection.decks.config_dict_for_deck_id(did)

    @util.api()
    def saveDeckConfig(self, config):
        collection = self.collection()

        config["id"] = int(config["id"])
        config["mod"] = int_time()
        config["usn"] = collection.usn()
        if config["id"] not in [c["id"] for c in collection.decks.all_config()]:
            return False
        try:
            collection.decks.update_config(config)
        except Exception:
            return False
        self.scheduleRefresh()
        return True

    @util.api()
    def setDeckConfigId(self, decks, configId):
        configId = int(configId)
        collection = self.collection()
        if configId not in [c["id"] for c in collection.decks.all_config()]:
            return False

        deck_dicts = []
        for deck in decks:
            did = collection.decks.id(deck, create=False)
            if not did:
                return False
            deck_dict = collection.decks.get(did, default=False)
            if deck_dict is None or deck_dict.get("dyn"):
                return False
            deck_dicts.append(deck_dict)

        for deck_dict in deck_dicts:
            try:
                collection.decks.set_config_id_for_deck_dict(deck_dict, configId)
            except Exception:
                return False

        if deck_dicts:
            self.scheduleRefresh()
        return True

    @util.api()
    def cloneDeckConfigId(self, name, cloneFrom="1"):
        configId = int(cloneFrom)
        collection = self.collection()
        if configId not in [c["id"] for c in collection.decks.all_config()]:
            return False

        config = collection.decks.get_config(configId)
        return collection.decks.add_config_returning_id(name, config)

    @util.api()
    def removeDeckConfigId(self, configId):
        collection = self.collection()
        if int(configId) not in [c["id"] for c in collection.decks.all_config()]:
            return False

        collection.decks.remove_config(configId)
        return True

    @util.api()
    def getDeckStats(self, decks):
        collection = self.collection()
        scheduler = self.scheduler()
        responseDict = {}
        deckIds = [collection.decks.id(d) for d in decks]

        allDeckNodes = self.collectDeckTreeChildren(scheduler.deck_due_tree())
        for deckId, deckNode in allDeckNodes.items():
            if deckId in deckIds:
                responseDict[deckId] = self.deckStatsToJson(deckNode)
        return responseDict

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
            allowed_roots.append(Path(vault_root).resolve())
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise Exception("media path is outside allowed directories")
        if not resolved.is_file():
            raise Exception(f"media file not found: {path}")
        return resolved

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
        filename = os.path.basename(filename)
        filename = unicodedata.normalize("NFC", filename)
        filename = self.media().stripIllegal(filename)

        path = os.path.join(self.media().dir(), filename)
        if os.path.exists(path):
            with open(path, "rb") as file:
                return base64.b64encode(file.read()).decode("ascii")

        return False

    @util.api()
    def getMediaFilesNames(self, pattern="*"):
        if (
            not isinstance(pattern, str)
            or os.path.isabs(pattern)
            or "/" in pattern
            or "\\" in pattern
            or ".." in pattern
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

    @util.api()
    def addNote(self, note):
        ankiNote = self.createNote(note)

        collection = self.collection()
        # Anki >= 25: note.note_type() returns a fresh copy of the notetype, so the
        # `did` set in createNote never reaches the legacy collection.addNote(),
        # which then files the note under the notetype's stored deck (Default).
        # Resolve the deck again and pass it explicitly.
        deck = collection.decks.by_name(note["deckName"])
        if deck is None:
            raise Exception("deck was not found: {}".format(note["deckName"]))
        self.startEditing()
        collection.add_note(ankiNote, deck["id"])
        if len(ankiNote.cards()) < 1:
            collection.remove_notes([ankiNote.id])
            raise Exception(
                "The field values you have provided would make an empty question on all cards."
            )
        self.stopEditing()

        return ankiNote.id

    def addMediaFromNote(self, ankiNote, note):
        audioObjectOrList = note.get("audio")
        self.addMedia(ankiNote, audioObjectOrList, util.MediaType.Audio)

        videoObjectOrList = note.get("video")
        self.addMedia(ankiNote, videoObjectOrList, util.MediaType.Video)

        pictureObjectOrList = note.get("picture")
        self.addMedia(ankiNote, pictureObjectOrList, util.MediaType.Picture)

    def addMedia(self, ankiNote, mediaObjectOrList, mediaType):
        if mediaObjectOrList is None:
            return

        if isinstance(mediaObjectOrList, list):
            mediaList = mediaObjectOrList
        else:
            mediaList = [mediaObjectOrList]

        for media in mediaList:
            if media is not None and len(media["fields"]) > 0:
                try:
                    mediaFilename = self.storeMediaFile(
                        media["filename"],
                        data=media.get("data"),
                        path=media.get("path"),
                        url=media.get("url"),
                        skipHash=media.get("skipHash"),
                        deleteExisting=media.get("deleteExisting"),
                    )

                    if mediaFilename is not None:
                        for field in media["fields"]:
                            if field in ankiNote:
                                if mediaType is util.MediaType.Picture:
                                    ankiNote[field] += f'<img src="{mediaFilename}">'
                                elif (
                                    mediaType is util.MediaType.Audio
                                    or mediaType is util.MediaType.Video
                                ):
                                    ankiNote[field] += f"[sound:{mediaFilename}]"

                except Exception as e:
                    errorMessage = (
                        str(e).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    )
                    for field in media["fields"]:
                        if field in ankiNote:
                            ankiNote[field] += errorMessage

    @util.api()
    def canAddNote(self, note):
        try:
            return bool(self.createNote(note))
        except:
            return False

    @util.api()
    def updateNoteFields(self, note):
        ankiNote = self.getNote(note["id"])

        self.startEditing()
        for name, value in note["fields"].items():
            if name in ankiNote:
                ankiNote[name] = value

        audioObjectOrList = note.get("audio")
        self.addMedia(ankiNote, audioObjectOrList, util.MediaType.Audio)

        videoObjectOrList = note.get("video")
        self.addMedia(ankiNote, videoObjectOrList, util.MediaType.Video)

        pictureObjectOrList = note.get("picture")
        self.addMedia(ankiNote, pictureObjectOrList, util.MediaType.Picture)

        self.collection().update_note(ankiNote, skip_undo_entry=True)
        self.stopEditing()

    @util.api()
    def updateNote(self, note):
        updated = False
        if "fields" in note.keys():
            self.updateNoteFields(note)
            updated = True
        if "tags" in note.keys():
            self.updateNoteTags(note["id"], note["tags"])
            updated = True
        if not updated:
            raise Exception('Must provide a "fields" or "tags" property.')

    @util.api()
    def updateNoteTags(self, note, tags):
        if type(tags) == str:
            tags = [tags]
        if type(tags) != list or not all(type(t) == str for t in tags):
            raise Exception("Must provide tags as a list of strings")

        for old_tag in self.getNoteTags(note):
            self.removeTags([note], old_tag)
        for new_tag in tags:
            self.addTags([note], new_tag)

    @util.api()
    def getNoteTags(self, note):
        return self.getNote(note).tags

    @util.api()
    def addTags(self, notes, tags, add=True):
        self.startEditing()
        # What tags.bulkAdd(notes, tags, add) does, without its deprecation warning.
        if add:
            self.collection().tags.bulk_add(notes, tags)
        else:
            self.collection().tags.bulk_remove(notes, tags)
        self.stopEditing()

    @util.api()
    def removeTags(self, notes, tags):
        return self.addTags(notes, tags, False)

    @util.api()
    def getTags(self):
        return self.collection().tags.all()

    @util.api()
    def clearUnusedTags(self):
        self.collection().tags.clear_unused_tags()

    @util.api()
    def replaceTags(self, notes, tag_to_replace, replace_with_tag):
        self.window().progress.start()

        for nid in notes:
            try:
                note = self.getNote(nid)
            except NotFoundError:
                continue

            if note.has_tag(tag_to_replace):
                note.remove_tag(tag_to_replace)
                note.add_tag(replace_with_tag)
                self.collection().update_note(note, skip_undo_entry=True)

        self.window().progress.finish()
        self.window().reset()

    @util.api()
    def replaceTagsInAllNotes(self, tag_to_replace, replace_with_tag):
        self.window().progress.start()

        collection = self.collection()
        for nid in collection.db.list("select id from notes"):
            note = self.getNote(nid)
            if note.has_tag(tag_to_replace):
                note.remove_tag(tag_to_replace)
                note.add_tag(replace_with_tag)
                collection.update_note(note, skip_undo_entry=True)

        self.window().progress.finish()
        self.window().reset()

    @util.api()
    def setEaseFactors(self, cards, easeFactors):
        couldSetEaseFactors = []
        for i, card in enumerate(cards):
            try:
                ankiCard = self.getCard(card)
            except NotFoundError:
                couldSetEaseFactors.append(False)
                continue

            couldSetEaseFactors.append(True)
            ankiCard.factor = easeFactors[i]
            self.collection().update_card(ankiCard, skip_undo_entry=True)

        return couldSetEaseFactors

    @util.api()
    def setSpecificValueOfCard(self, card, keys, newValues, warning_check=False):
        if isinstance(card, list):
            print("card has to be int, not list")
            return False

        if not isinstance(keys, list) or not isinstance(newValues, list):
            print("keys and newValues have to be lists.")
            return False

        if len(newValues) != len(keys):
            print("Invalid list lengths.")
            return False

        for key in keys:
            if key in [
                "did",
                "id",
                "ivl",
                "lapses",
                "left",
                "mod",
                "nid",
                "odid",
                "odue",
                "ord",
                "queue",
                "reps",
                "type",
                "usn",
            ]:
                if warning_check is False:
                    return False

        result = []
        try:
            ankiCard = self.getCard(card)
            for i, key in enumerate(keys):
                setattr(ankiCard, key, newValues[i])
            self.collection().update_card(ankiCard, skip_undo_entry=True)
            result.append(True)
        except Exception as e:
            result.append([False, str(e)])
        return result

    @util.api()
    def getEaseFactors(self, cards):
        easeFactors = []
        for card in cards:
            try:
                ankiCard = self.getCard(card)
            except NotFoundError:
                easeFactors.append(None)
                continue

            easeFactors.append(ankiCard.factor)

        return easeFactors

    @util.api()
    def suspend(self, cards, suspend=True):
        # The queue of every integer card id in one query; any other id, or one the query
        # does not find, goes through suspended() and raises what it raises.
        cardIds = {card for card in cards if type(card) is int}
        queues = {}
        if cardIds:
            queues = dict(
                self.database().all(
                    "select id, queue from cards where id in " + anki.utils.ids2str(cardIds)
                )
            )

        to_change = []
        for card in cards:
            if type(card) is int and card in queues:
                isSuspended = queues[card] == -1
            else:
                isSuspended = self.suspended(card)
            if isSuspended != suspend:
                to_change.append(card)

        if len(to_change) == 0:
            return False

        scheduler = self.scheduler()
        self.startEditing()
        if suspend:
            scheduler.suspend_cards(to_change)
        else:
            scheduler.unsuspend_cards(to_change)
        self.stopEditing()

        return True

    @util.api()
    def unsuspend(self, cards):
        self.suspend(cards, False)

    @util.api()
    def suspended(self, card):
        card = self.getCard(card)
        return card.queue == -1

    @util.api()
    def areSuspended(self, cards):
        suspended = []
        for card in cards:
            try:
                suspended.append(self.suspended(card))
            except NotFoundError:
                suspended.append(None)

        return suspended

    @util.api()
    def areDue(self, cards):
        due = []
        for card in cards:
            if self.findCards(f"cid:{card} is:new"):
                due.append(True)
            else:
                rows = self.collection().db.all(
                    "select id/1000.0, ivl from revlog where cid = ? order by id", card
                )
                if not rows:
                    due.append(bool(self.findCards(f"cid:{card} is:due")))
                else:
                    date, ivl = rows[-1]
                    if ivl >= -1200:
                        due.append(bool(self.findCards(f"cid:{card} is:due")))
                    else:
                        due.append(date - ivl <= time.time())

        return due

    @util.api()
    def getIntervals(self, cards, complete=False):
        intervals = []
        for card in cards:
            if self.findCards(f"cid:{card} is:new"):
                intervals.append(0)
            else:
                interval = self.collection().db.list(
                    "select ivl from revlog where cid = ? order by id", card
                )
                if not complete:
                    interval = interval[-1] if interval else 0
                intervals.append(interval)

        return intervals

    @util.api()
    def modelNames(self):
        # What models.allNames() returns, without its two deprecation warnings.
        names = self.collection().models.all_names_and_ids()
        return [name.name for name in names]

    @util.api()
    def createModel(self, modelName, inOrderFields, cardTemplates, css=None, isCloze=False):
        # https://github.com/dae/anki/blob/b06b70f7214fb1f2ce33ba06d2b095384b81f874/anki/stdmodels.py
        if len(inOrderFields) == 0:
            raise Exception("Must provide at least one field for inOrderFields")
        if len(cardTemplates) == 0:
            raise Exception("Must provide at least one card for cardTemplates")
        if modelName in self.modelNames():
            raise Exception("Model name already exists")

        collection = self.collection()
        mm = collection.models

        # Generate new Note
        m = mm.new(modelName)
        if isCloze:
            m["type"] = MODEL_CLOZE

        # Create fields and add them to Note
        for field in inOrderFields:
            fm = mm.new_field(field)
            mm.addField(m, fm)

        # Add shared css to model if exists. Use default otherwise
        if css is not None:
            m["css"] = css

        # Generate new card template(s)
        cardCount = 1
        for card in cardTemplates:
            cardName = "Card " + str(cardCount)
            if "Name" in card:
                cardName = card["Name"]

            t = mm.new_template(cardName)
            cardCount += 1
            t["qfmt"] = card["Front"]
            t["afmt"] = card["Back"]
            mm.addTemplate(m, t)

        mm.add(m)
        return m

    @util.api()
    def modelNamesAndIds(self):
        models = {}
        for model in self.modelNames():
            models[model] = int(self.collection().models.by_name(model)["id"])

        return models

    @util.api()
    def modelNameFromId(self, modelId):
        model = self.collection().models.get(modelId)
        if model is None:
            raise Exception(f"model was not found: {modelId}")
        else:
            return model["name"]

    @util.api()
    def modelFieldNames(self, modelName):
        model = self.collection().models.by_name(modelName)
        if model is None:
            raise Exception(f"model was not found: {modelName}")
        else:
            return [field["name"] for field in model["flds"]]

    @util.api()
    def modelFieldDescriptions(self, modelName):
        model = self.collection().models.by_name(modelName)
        if model is None:
            raise Exception(f"model was not found: {modelName}")
        else:
            return [field["description"] for field in model["flds"]]

    @util.api()
    def modelFieldFonts(self, modelName):
        model = self.getModel(modelName)

        fonts = {}
        for field in model["flds"]:
            fonts[field["name"]] = {
                "font": field["font"],
                "size": field["size"],
            }

        return fonts

    @util.api()
    def modelFieldsOnTemplates(self, modelName):
        model = self.collection().models.by_name(modelName)
        if model is None:
            raise Exception(f"model was not found: {modelName}")

        templates = {}
        for template in model["tmpls"]:
            fields = []
            for side in ["qfmt", "afmt"]:
                fieldsForSide = []

                # based on _fieldsOnTemplate from aqt/clayout.py
                matches = re.findall("{{[^#/}]+?}}", template[side])
                for match in matches:
                    # remove braces and modifiers
                    match = re.sub(r"[{}]", "", match)
                    match = match.split(":")[-1]

                    # for the answer side, ignore fields present on the question side + the FrontSide field
                    if match == "FrontSide" or side == "afmt" and match in fields[0]:
                        continue
                    fieldsForSide.append(match)

                fields.append(fieldsForSide)

            templates[template["name"]] = fields

        return templates

    @util.api()
    def modelTemplates(self, modelName):
        model = self.collection().models.by_name(modelName)
        if model is None:
            raise Exception(f"model was not found: {modelName}")

        templates = {}
        for template in model["tmpls"]:
            templates[template["name"]] = {"Front": template["qfmt"], "Back": template["afmt"]}

        return templates

    @util.api()
    def modelStyling(self, modelName):
        model = self.collection().models.by_name(modelName)
        if model is None:
            raise Exception(f"model was not found: {modelName}")

        return {"css": model["css"]}

    @util.api()
    def updateModelTemplates(self, model):
        models = self.collection().models
        ankiModel = models.by_name(model["name"])
        if ankiModel is None:
            raise Exception("model was not found: {}".format(model["name"]))

        templates = model["templates"]
        for ankiTemplate in ankiModel["tmpls"]:
            template = templates.get(ankiTemplate["name"])
            if template:
                qfmt = template.get("Front")
                if qfmt:
                    ankiTemplate["qfmt"] = qfmt

                afmt = template.get("Back")
                if afmt:
                    ankiTemplate["afmt"] = afmt

        self.save_model(models, ankiModel)

    @util.api()
    def updateModelStyling(self, model):
        models = self.collection().models
        ankiModel = models.by_name(model["name"])
        if ankiModel is None:
            raise Exception("model was not found: {}".format(model["name"]))

        ankiModel["css"] = model["css"]

        self.save_model(models, ankiModel)

    @util.api()
    def findAndReplaceInModels(
        self, modelName, findText, replaceText, front=True, back=True, css=True
    ):
        if not modelName:
            ankiModel = self.modelNames()
        else:
            model = self.collection().models.by_name(modelName)
            if model is None:
                raise Exception(f"model was not found: {modelName}")
            ankiModel = [modelName]
        updatedModels = 0
        for model in ankiModel:
            model = self.collection().models.by_name(model)
            checkForText = False
            if css and findText in model["css"]:
                checkForText = True
                model["css"] = model["css"].replace(findText, replaceText)
            for tmpls in model.get("tmpls"):
                if front and findText in tmpls["qfmt"]:
                    checkForText = True
                    tmpls["qfmt"] = tmpls["qfmt"].replace(findText, replaceText)
                if back and findText in tmpls["afmt"]:
                    checkForText = True
                    tmpls["afmt"] = tmpls["afmt"].replace(findText, replaceText)
            self.save_model(self.collection().models, model)
            if checkForText:
                updatedModels += 1
        return updatedModels

    @util.api()
    def modelTemplateRename(self, modelName, oldTemplateName, newTemplateName):
        mm = self.collection().models
        model = self.getModel(modelName)
        ankiTemplate = self.getTemplate(model, oldTemplateName)

        ankiTemplate["name"] = newTemplateName
        self.save_model(mm, model)

    @util.api()
    def modelTemplateReposition(self, modelName, templateName, index):
        mm = self.collection().models
        model = self.getModel(modelName)
        ankiTemplate = self.getTemplate(model, templateName)

        mm.reposition_template(model, ankiTemplate, index)
        self.save_model(mm, model)

    @util.api()
    def modelTemplateAdd(self, modelName, template):
        # "Name", "Front", "Back" borrows from `createModel`
        mm = self.collection().models
        model = self.getModel(modelName)
        name = template["Name"]
        qfmt = template["Front"]
        afmt = template["Back"]

        # updates the template if it already exists
        for ankiTemplate in model["tmpls"]:
            if ankiTemplate["name"] == name:
                ankiTemplate["qfmt"] = qfmt
                ankiTemplate["afmt"] = afmt
                self.save_model(mm, model)
                self.scheduleRefresh()
                return

        ankiTemplate = mm.new_template(name)
        ankiTemplate["qfmt"] = qfmt
        ankiTemplate["afmt"] = afmt
        mm.add_template(model, ankiTemplate)

        self.save_model(mm, model)
        self.scheduleRefresh()

    @util.api()
    def modelTemplateRemove(self, modelName, templateName):
        mm = self.collection().models
        model = self.getModel(modelName)
        ankiTemplate = self.getTemplate(model, templateName)

        mm.remove_template(model, ankiTemplate)
        self.save_model(mm, model)

    @util.api()
    def modelFieldRename(self, modelName, oldFieldName, newFieldName):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, oldFieldName)

        mm.renameField(model, field, newFieldName)

        self.save_model(mm, model)

    @util.api()
    def modelFieldReposition(self, modelName, fieldName, index):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)

        mm.reposition_field(model, field, index)

        self.save_model(mm, model)

    @util.api()
    def modelFieldAdd(self, modelName, fieldName, index=None):
        mm = self.collection().models
        model = self.getModel(modelName)

        modified = False
        # only adds the field if it doesn't already exist
        fieldMap = mm.field_map(model)
        if fieldName not in fieldMap:
            field = mm.new_field(fieldName)
            mm.add_field(model, field)
            modified = True

        # repositions, even if the field already exists
        if index is not None:
            fieldMap = mm.field_map(model)
            newField = fieldMap[fieldName][1]
            mm.reposition_field(model, newField, index)
            modified = True

        if modified:
            self.save_model(mm, model)
            self.scheduleRefresh()

    @util.api()
    def modelFieldRemove(self, modelName, fieldName):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)

        mm.remove_field(model, field)

        self.save_model(mm, model)

    @util.api()
    def modelFieldSetFont(self, modelName, fieldName, font):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)

        if not isinstance(font, str):
            raise Exception(f"font should be a string: {font}")

        field["font"] = font

        self.save_model(mm, model)

    @util.api()
    def modelFieldSetFontSize(self, modelName, fieldName, fontSize):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)

        if not isinstance(fontSize, int):
            raise Exception(f"fontSize should be an integer: {fontSize}")

        field["size"] = fontSize

        self.save_model(mm, model)

    @util.api()
    def modelFieldSetDescription(self, modelName, fieldName, description):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)

        if not isinstance(description, str):
            raise Exception(f"description should be a string: {description}")

        field["description"] = description
        self.save_model(mm, model)
        return True

    @util.api()
    def deckNameFromId(self, deckId):
        deck = self.collection().decks.get(deckId)
        if deck is None:
            raise Exception(f"deck was not found: {deckId}")

        return deck["name"]

    @util.api()
    def findNotes(self, query=None):
        if query is None:
            return []

        return list(map(int, self.collection().find_notes(query)))

    @util.api()
    def findCards(self, query=None):
        if query is None:
            return []

        return list(map(int, self.collection().find_cards(query)))

    @util.api()
    def cardsInfo(self, cards):
        # Deck name per deck id, looked up once per request instead of once per card.
        deckNames = {}

        def deckName(did):
            if did not in deckNames:
                deckNames[did] = self.deckNameFromId(did)
            return deckNames[did]

        result = []
        for cid in cards:
            try:
                card = self.getCard(cid)
                model = card.note_type()
                note = card.note()
                fields = {}
                for info in model["flds"]:
                    order = info["ord"]
                    name = info["name"]
                    fields[name] = {"value": note.fields[order], "order": order}

                result.append(
                    {
                        "cardId": card.id,
                        "fields": fields,
                        "fieldOrder": card.ord,
                        "question": card.question(),
                        "answer": card.answer(),
                        "modelName": model["name"],
                        "ord": card.ord,
                        "deckName": deckName(card.did),
                        "css": model["css"],
                        "factor": card.factor,
                        # This factor is 10 times the ease percentage,
                        # so an ease of 310% would be reported as 3100
                        "interval": card.ivl,
                        "note": card.nid,
                        "type": card.type,
                        "queue": card.queue,
                        "due": card.due,
                        "reps": card.reps,
                        "lapses": card.lapses,
                        "left": card.left,
                        "mod": card.mod,
                    }
                )
            except NotFoundError:
                # Anki will give a NotFoundError if the card ID does not exist.
                # Best behavior is probably to add an 'empty card' to the
                # returned result, so that the items of the input and return
                # lists correspond.
                result.append({})

        return result

    @util.api()
    def cardsModTime(self, cards):
        result = []
        for cid in cards:
            try:
                card = self.getCard(cid)
                result.append(
                    {
                        "cardId": card.id,
                        "mod": card.mod,
                    }
                )
            except NotFoundError:
                # Anki will give a NotFoundError if the card ID does not exist.
                # Best behavior is probably to add an 'empty card' to the
                # returned result, so that the items of the input and return
                # lists correspond.
                result.append({})
        return result

    @util.api()
    def forgetCards(self, cards):
        if not cards:
            return
        self.scheduler().schedule_cards_as_new(cards)
        self.scheduleRefresh()

    @util.api()
    def relearnCards(self, cards):
        if not cards:
            return
        col = self.collection()
        col.sched.remFromDyn(list(cards))
        updated = []
        for cid in cards:
            card = self.getCard(cid)
            card.type = 3
            card.queue = 1
            updated.append(card)
        col.update_cards(updated, skip_undo_entry=True)
        self.scheduleRefresh()

    @util.api()
    def answerCards(self, answers):
        scheduler = self.scheduler()
        success = []
        for answer in answers:
            try:
                cid = answer["cardId"]
                ease = answer["ease"]
                card = self.getCard(cid)
                card.start_timer()
                scheduler.answerCard(card, ease)
                success.append(True)
            except NotFoundError:
                success.append(False)

        return success

    @util.api()
    def cardReviews(self, deck, startID):
        return self.database().all(
            "select id, cid, usn, ease, ivl, lastIvl, factor, time, type from revlog "
            "where id>? and cid in (select id from cards where did=?)",
            startID,
            self.decks().id(deck),
        )

    @util.api()
    def getReviewsOfCards(self, cards):
        db = self.database()
        COLUMNS = ["id", "usn", "ease", "ivl", "lastIvl", "factor", "time", "type"]
        revlog_cols = {row[1] for row in db.all("pragma table_info(revlog)")}
        if "data" in revlog_cols:
            COLUMNS.append("data")
        QUERY = "select {} from revlog where cid = ?".format(", ".join(COLUMNS))

        # One query for every integer card id. revlog.id is its rowid, so the per-card
        # query above returns a card's rows in id order; the batch keeps that order.
        cardIds = {card for card in cards if type(card) is int}
        rows = {card: [] for card in cardIds}
        if cardIds:
            batch = "select cid, {} from revlog where cid in {} order by cid, id".format(
                ", ".join(COLUMNS), anki.utils.ids2str(cardIds)
            )
            for row in db.all(batch):
                rows[row[0]].append(row[1:])

        result = {}
        for card in cards:
            if type(card) is int:
                query_result = rows[card]
            else:
                query_result = db.all(QUERY, card)
            result[card] = [dict(zip(COLUMNS, row, strict=False)) for row in query_result]

        return result

    @util.api()
    def reloadCollection(self):
        # Anki resets its queues after every operation, so there is nothing left to reload;
        # the call still fails when no collection is open.
        self.collection()

    @util.api()
    def getLatestReviewID(self, deck):
        return (
            self.database().scalar(
                "select max(id) from revlog where cid in (select id from cards where did=?)",
                self.decks().id(deck),
            )
            or 0
        )

    @util.api()
    def insertReviews(self, reviews):
        if not reviews:
            return
        validated = []
        for row in reviews:
            if not isinstance(row, (list, tuple)) or len(row) != 9:
                raise Exception("each review row must contain exactly 9 integer fields")
            if not all(type(val) is int for val in row):
                raise Exception("each review row field must be an integer")
            validated.append(tuple(row))
        sql = (
            "insert into revlog(id,cid,usn,ease,ivl,lastIvl,factor,time,type) "
            "values (?,?,?,?,?,?,?,?,?)"
        )
        db = self.database()
        for row in validated:
            db.execute(sql, *row)
        self.scheduleRefresh()

    @util.api()
    def notesInfo(self, notes):
        cardIds = self.cardIdsOfNotes(notes)
        result = []
        for nid in notes:
            try:
                note = self.getNote(nid)
                model = note.note_type()

                fields = {}
                for info in model["flds"]:
                    order = info["ord"]
                    name = info["name"]
                    fields[name] = {"value": note.fields[order], "order": order}

                result.append(
                    {
                        "noteId": note.id,
                        "tags": note.tags,
                        "fields": fields,
                        "modelName": model["name"],
                        "cards": list(cardIds[note.id])
                        if note.id in cardIds
                        else self.collection().db.list(
                            "select id from cards where nid = ? order by ord", note.id
                        ),
                    }
                )
            except NotFoundError:
                # Anki will give a NotFoundError if the note ID does not exist.
                # Best behavior is probably to add an 'empty card' to the
                # returned result, so that the items of the input and return
                # lists correspond.
                result.append({})

        return result

    def cardIdsOfNotes(self, notes):
        """{note id: its card ids by ord} for the integer ids in `notes`, in one query.

        Same rows as `select id from cards where nid = ? order by ord` per note. A
        non-integer id gets no entry; the caller queries it on its own.
        """
        noteIds = {nid for nid in notes if type(nid) is int}
        cardIds = {nid: [] for nid in noteIds}
        if noteIds:
            for nid, cid in self.collection().db.all(
                "select nid, id from cards where nid in %s order by nid, ord"
                % anki.utils.ids2str(noteIds)
            ):
                cardIds[nid].append(cid)
        return cardIds

    @util.api()
    def deleteNotes(self, notes):
        self.collection().remove_notes(notes)
        self.scheduleRefresh()

    @util.api()
    def removeEmptyNotes(self):
        for model in self.collection().models.all():
            if self.collection().models.use_count(model) == 0:
                self.collection().models.remove(model["id"])
        self.scheduleRefresh()

    @util.api()
    def cardsToNotes(self, cards):
        return self.collection().db.list(
            "select distinct nid from cards where id in " + anki.utils.ids2str(cards)
        )

    @util.api()
    def guiBrowse(self, query=None):
        browser = aqt.dialogs.open("Browser", self.window())
        browser.activateWindow()

        if query is not None:
            browser.form.searchEdit.lineEdit().setText(query)
            browser.onSearchActivated()

        return self.findCards(query)

    @util.api()
    def guiEditNote(self, note):
        Edit.open_dialog_and_show_note_with_id(note)

    @util.api()
    def guiSelectedNotes(self):
        (creator, instance) = aqt.dialogs._dialogs["Browser"]
        if instance is None:
            return []
        return instance.selectedNotes()

    @util.api()
    def guiAddCards(self, note=None):
        if note is not None:
            collection = self.collection()

            deck = collection.decks.by_name(note["deckName"])
            if deck is None:
                raise Exception("deck was not found: {}".format(note["deckName"]))

            collection.decks.select(deck["id"])
            savedMid = deck.pop("mid", None)

            model = collection.models.by_name(note["modelName"])
            if model is None:
                raise Exception("model was not found: {}".format(note["modelName"]))

            collection.models.set_current(model)
            collection.models.update(model)

            ankiNote = anki.notes.Note(collection, model)

            # fill out card beforehand, so we can be sure of the note id
            if "fields" in note:
                for name, value in note["fields"].items():
                    if name in ankiNote:
                        ankiNote[name] = value

            self.addMediaFromNote(ankiNote, note)

            if "tags" in note:
                ankiNote.tags = note["tags"]

            def openNewWindow():
                nonlocal ankiNote

                addCards = aqt.dialogs.open("AddCards", self.window())

                if savedMid:
                    deck["mid"] = savedMid

                addCards.editor.set_note(ankiNote)

                addCards.activateWindow()

                aqt.dialogs.open("AddCards", self.window())
                addCards.setAndFocusNote(addCards.editor.note)

            currentWindow = aqt.dialogs._dialogs["AddCards"][1]

            if currentWindow is not None:
                currentWindow.closeWithCallback(openNewWindow)
            else:
                openNewWindow()

            return ankiNote.id

        else:
            addCards = aqt.dialogs.open("AddCards", self.window())
            addCards.activateWindow()

            return addCards.editor.note.id

    @util.api()
    def guiReviewActive(self):
        return self.reviewer().card is not None and self.window().state == "review"

    @util.api()
    def guiCurrentCard(self):
        if not self.guiReviewActive():
            raise Exception("Gui review is not currently active.")

        reviewer = self.reviewer()
        card = reviewer.card
        model = card.note_type()
        note = card.note()

        fields = {}
        for info in model["flds"]:
            order = info["ord"]
            name = info["name"]
            fields[name] = {"value": note.fields[order], "order": order}

        buttonList = reviewer._answerButtonList()
        return {
            "cardId": card.id,
            "fields": fields,
            "fieldOrder": card.ord,
            "question": card.question(),
            "answer": card.answer(),
            "buttons": [b[0] for b in buttonList],
            "nextReviews": [
                reviewer.mw.col.sched.nextIvlStr(reviewer.card, b[0], True) for b in buttonList
            ],
            "modelName": model["name"],
            "deckName": self.deckNameFromId(card.did),
            "css": model["css"],
            "template": card.template()["name"],
        }

    @util.api()
    def guiStartCardTimer(self):
        if not self.guiReviewActive():
            return False

        card = self.reviewer().card
        if card is not None:
            card.start_timer()
            return True

        return False

    @util.api()
    def guiShowQuestion(self):
        if self.guiReviewActive():
            self.reviewer()._showQuestion()
            return True

        return False

    @util.api()
    def guiShowAnswer(self):
        if self.guiReviewActive():
            self.window().reviewer._showAnswer()
            return True

        return False

    @util.api()
    def guiAnswerCard(self, ease):
        if not self.guiReviewActive():
            return False

        reviewer = self.reviewer()
        if reviewer.state != "answer":
            return False
        if ease <= 0 or ease > self.scheduler().answerButtons(reviewer.card):
            return False

        reviewer._answerCard(ease)
        return True

    @util.api()
    def guiUndo(self):
        self.window().undo()
        return True

    @util.api()
    def guiDeckOverview(self, name):
        collection = self.collection()
        if collection is not None:
            deck = collection.decks.by_name(name)
            if deck is not None:
                collection.decks.select(deck["id"])
                self.window().onOverview()
                return True

        return False

    @util.api()
    def guiDeckBrowser(self):
        self.window().moveToState("deckBrowser")

    @util.api()
    def guiDeckReview(self, name):
        if self.guiDeckOverview(name):
            self.window().moveToState("review")
            return True

        return False

    @util.api()
    def guiImportFile(self, path=None):
        """
        Open Import File (Ctrl+Shift+I) dialog with provided file path.
        If no path is given, the user will be prompted to select a file.

        path: string
            import file path, note on Windows you must use forward slashes.
        """
        from aqt.import_export.importing import import_file, prompt_for_file_then_import

        WindowOnTopFlag = Qt.WindowType.WindowStaysOnTopHint

        # Bring window to top for user to review import settings.
        try:
            # [Step 1/2] set always on top flag, show window (it stays on top for now)
            self.window().setWindowFlags(self.window().windowFlags() | WindowOnTopFlag)
            self.window().show()
        finally:
            # [Step 2/2] clear always on top flag, show window (it doesn't stay on top anymore)
            self.window().setWindowFlags(self.window().windowFlags() & ~WindowOnTopFlag)
            self.window().show()

        if path is None:
            prompt_for_file_then_import(self.window())
        else:
            import_file(self.window(), path)

    @util.api()
    def guiExitAnki(self):
        QTimer.singleShot(1000, self.window().close)

    @util.api()
    def guiCheckDatabase(self):
        self.window().onCheckDB()
        return True

    @util.api()
    def addNotes(self, notes):
        results = []
        for note in notes:
            try:
                results.append(self.addNote(note))
            except:
                results.append(None)

        return results

    @util.api()
    def canAddNotes(self, notes):
        results = []
        for note in notes:
            results.append(self.canAddNote(note))

        return results

    @util.api()
    def exportPackage(self, deck, path, includeSched=False):
        collection = self.collection()
        if collection is not None:
            deck = collection.decks.by_name(deck)
            if deck is not None:
                exporter = AnkiPackageExporter(collection)
                exporter.did = deck["id"]
                exporter.includeSched = includeSched
                exporter.exportInto(path)
                return True

        return False

    @util.api()
    def importPackage(self, path):
        collection = self.collection()
        if collection is not None:
            try:
                self.startEditing()
                importer = AnkiPackageImporter(collection, path)
                importer.run()
            except:
                self.stopEditing()
                raise
            else:
                self.stopEditing()
                return True

        return False

    @util.api()
    def apiReflect(self, scopes=None, actions=None):
        if not isinstance(scopes, list):
            raise Exception("scopes has invalid value")
        if not (actions is None or isinstance(actions, list)):
            raise Exception("actions has invalid value")

        cls = type(self)
        scopes2 = []
        result = {"scopes": scopes2}

        if "actions" in scopes:
            if actions is None:
                actions = dir(cls)

            methodNames = []
            for methodName in actions:
                if not isinstance(methodName, str):
                    continue
                method = getattr(cls, methodName, None)
                if method is not None and getattr(method, "api", False):
                    methodNames.append(methodName)

            scopes2.append("actions")
            result["actions"] = methodNames

        return result

    @util.api()
    def changeNoteType(self, notes, modelName, newFields):
        """Convert notes of one type to modelName in place; cards and reviews are kept.

        newFields: for each field of modelName, the index of the old field it takes, or -1.
        """
        if not notes:
            raise Exception("no notes provided")
        col = self.collection()
        new_type = col.models.by_name(modelName)
        if new_type is None:
            raise Exception(f"model was not found: {modelName}")
        if not isinstance(newFields, list) or len(newFields) != len(new_type["flds"]):
            raise Exception(
                f"newFields length must match {modelName} field count ({len(new_type['flds'])})"
            )
        old_ids = {col.get_note(int(nid)).mid for nid in notes}
        if len(old_ids) != 1:
            raise Exception("changeNoteType takes notes of one type at a time")
        old_id = old_ids.pop()
        if old_id == new_type["id"] and newFields == list(range(len(new_type["flds"]))):
            return True
        info = col.models.change_notetype_info(
            old_notetype_id=old_id, new_notetype_id=new_type["id"]
        )
        request = info.input
        request.note_ids.extend(int(nid) for nid in notes)
        request.new_fields[:] = newFields
        col.models.change_notetype_of_notes(request)
        self.scheduleRefresh()
        return True

    @util.api()
    def getFSRSStats(self, cards=None):
        """FSRS memory state per card: difficulty (1-10) and stability (days), or None."""
        col = self.collection()
        result = []
        for cid in cards or []:
            item = {"cardId": cid, "difficulty": None, "stability": None}
            try:
                int_cid = int(cid)
                item["cardId"] = int_cid
                state = col.get_card(int_cid).memory_state
            except Exception as e:
                item["error"] = str(e)
                state = None
            if state:
                item["difficulty"] = state.difficulty
                item["stability"] = state.stability
            result.append(item)
        return result


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
