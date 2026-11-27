"""GUI, profile, sync, permission, batch dispatch, and reflection AnkiConnect actions."""

import anki.notes
import aqt
from aqt.qt import QCheckBox, QMessageBox, Qt, QTimer

from . import util
from .edit import Edit


class GuiActions:
    """Anki GUI, profile, permission, multi, and reflection actions."""

    @util.api()
    def version(self):
        return util.setting("apiVersion")

    @util.api()
    def requestPermission(self, origin, allowed):
        results = {"permission": "denied"}
        origin = origin.replace("\r", "").replace("\n", "") if isinstance(origin, str) else ""

        if allowed:
            results = {
                "permission": "granted",
                "requireApikey": bool(util.setting("apiKey")),
                "version": util.setting("apiVersion"),
            }

        elif not origin or origin in util.setting("ignoreOriginList") or self._permissionPromptOpen:
            pass  # defaults to denied

        else:
            self._permissionPromptOpen = True
            try:
                msg = QMessageBox(None)
                msg.setWindowTitle("A website wants to access to Anki")
                msg.setText(
                    f'"{origin}" requests permission to use Anki through AnkiConnect. '
                    "Do you want to give it access?"
                )
                msg.setInformativeText(
                    "By granting permission, you'll allow the website to modify your collection "
                    "on your behalf, including the execution of destructive actions such as deck "
                    "deletion."
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
        (_creator, instance) = aqt.dialogs._dialogs["Browser"]
        if instance is None:
            return []
        return instance.selected_notes()

    @util.api()
    def guiAddCards(self, note=None):
        if note is not None:
            collection = self.collection()

            deck = collection.decks.by_name(note["deckName"])
            if deck is None:
                raise Exception(f"deck was not found: {note['deckName']}")

            collection.decks.select(deck["id"])
            savedMid = deck.pop("mid", None)

            model = collection.models.by_name(note["modelName"])
            if model is None:
                raise Exception(f"model was not found: {note['modelName']}")

            collection.models.set_current(model)
            collection.models.update_dict(model)

            ankiNote = anki.notes.Note(collection, model)

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
        """Open Import File (Ctrl+Shift+I) dialog with provided file path."""
        from aqt.import_export.importing import import_file, prompt_for_file_then_import

        WindowOnTopFlag = Qt.WindowType.WindowStaysOnTopHint

        try:
            self.window().setWindowFlags(self.window().windowFlags() | WindowOnTopFlag)
            self.window().show()
        finally:
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
