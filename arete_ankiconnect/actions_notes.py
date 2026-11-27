"""Note and tag management AnkiConnect actions."""

import anki
import anki.notes
import anki.utils
from anki.errors import NotFoundError
from anki.utils import field_checksum

from . import util


class NoteActions:
    """Note CRUD, tag editing, and note search actions."""

    def _buildNote(self, note):
        collection = self.collection()

        model = collection.models.by_name(note["modelName"])
        if model is None:
            raise Exception(f"model was not found: {note['modelName']}")

        deck = collection.decks.by_name(note["deckName"])
        if deck is None:
            raise Exception(f"deck was not found: {note['deckName']}")

        ankiNote = anki.notes.Note(collection, model)
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
                            'option parameter "duplicateScopeOptions.checkAllModels" '
                            "must be boolean"
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
        if duplicateOrEmpty == 2:
            if allowDuplicate:
                return ankiNote, deck
            raise Exception("cannot create note because it is a duplicate")
        if duplicateOrEmpty == 0:
            return ankiNote, deck
        raise Exception("cannot create note for unknown reason")

    def createNote(self, note):
        ankiNote, _deck = self._buildNote(note)
        return ankiNote

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
        if duplicateScope != "deck" and not duplicateScopeCheckAllModels:
            return note.fields_check() or 0

        val = note.fields[0]
        if not val.strip():
            return 1
        csum = field_checksum(val)

        dids = None
        if duplicateScope == "deck":
            did = deck["id"]
            if duplicateScopeDeckName is not None:
                deck2 = collection.decks.by_name(duplicateScopeDeckName)
                if deck2 is None:
                    return 0
                did = deck2["id"]

            dids = {did: True}
            if duplicateScopeCheckChildren:
                for kv in collection.decks.children(did):
                    dids[kv[1]] = True

        query = "select id from notes where csum=?"
        queryArgs = [csum]
        if note.id:
            query += " and id!=?"
            queryArgs.append(note.id)
        if not duplicateScopeCheckAllModels:
            query += " and mid=?"
            queryArgs.append(note.mid)

        for noteId in note.col.db.list(query, *queryArgs):
            if dids is None:
                return 2
            for cardDeckId in note.col.db.list("select did from cards where nid=?", noteId):
                if cardDeckId in dids:
                    return 2

        return 0

    def cardIdsOfNotes(self, notes):
        """{note id: its card ids by ord} for the integer ids in `notes`, in one query.

        Same rows as `select id from cards where nid = ? order by ord` per note. A
        non-integer id gets no entry; the caller queries it on its own.
        """
        noteIds = {nid for nid in notes if type(nid) is int}
        cardIds = {nid: [] for nid in noteIds}
        if noteIds:
            for nid, cid in self.collection().db.all(
                "select nid, id from cards where nid in "
                f"{anki.utils.ids2str(noteIds)} order by nid, ord"
            ):
                cardIds[nid].append(cid)
        return cardIds

    @util.api()
    def addNote(self, note):
        ankiNote, deck = self._buildNote(note)
        collection = self.collection()
        collection.add_note(ankiNote, deck["id"])
        if len(ankiNote.cards()) < 1:
            collection.remove_notes([ankiNote.id])
            raise Exception(
                "The field values you have provided would make an empty question on all cards."
            )
        self.scheduleRefresh()
        return ankiNote.id

    @util.api()
    def addNotes(self, notes):
        results = []
        for note in notes:
            try:
                results.append(self.addNote(note))
            except Exception:
                results.append(None)
        return results

    @util.api()
    def canAddNote(self, note):
        try:
            return bool(self.createNote(note))
        except Exception:
            return False

    @util.api()
    def canAddNotes(self, notes):
        return [self.canAddNote(note) for note in notes]

    @util.api()
    def updateNoteFields(self, note):
        ankiNote = self.getNote(note["id"])

        for name, value in note["fields"].items():
            if name in ankiNote:
                ankiNote[name] = value

        self.addMediaFromNote(ankiNote, note)
        self.collection().update_note(ankiNote, skip_undo_entry=True)
        self.scheduleRefresh()

    @util.api()
    def updateNote(self, note):
        updated = False
        if "fields" in note:
            self.updateNoteFields(note)
            updated = True
        if "tags" in note:
            self.updateNoteTags(note["id"], note["tags"])
            updated = True
        if not updated:
            raise Exception('Must provide a "fields" or "tags" property.')

    @util.api()
    def updateNoteTags(self, note, tags):
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
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
        if add:
            self.collection().tags.bulk_add(notes, tags)
        else:
            self.collection().tags.bulk_remove(notes, tags)
        self.scheduleRefresh()

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
        try:
            for nid in notes:
                try:
                    note = self.getNote(nid)
                except NotFoundError:
                    continue

                if note.has_tag(tag_to_replace):
                    note.remove_tag(tag_to_replace)
                    note.add_tag(replace_with_tag)
                    self.collection().update_note(note, skip_undo_entry=True)
        finally:
            self.window().progress.finish()
        self.window().reset()

    @util.api()
    def replaceTagsInAllNotes(self, tag_to_replace, replace_with_tag):
        self.window().progress.start()
        try:
            collection = self.collection()
            for nid in collection.db.list("select id from notes"):
                note = self.getNote(nid)
                if note.has_tag(tag_to_replace):
                    note.remove_tag(tag_to_replace)
                    note.add_tag(replace_with_tag)
                    collection.update_note(note, skip_undo_entry=True)
        finally:
            self.window().progress.finish()
        self.window().reset()

    @util.api()
    def findNotes(self, query=None):
        if query is None:
            return []
        return list(map(int, self.collection().find_notes(query)))

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
                result.append({})

        return result

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
