"""Card inspection, scheduling, suspension, and revlog AnkiConnect actions."""

import time

import anki.utils
from anki.errors import NotFoundError

from . import util

_SENSITIVE_CARD_KEYS = frozenset(
    {
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
    }
)


class CardActions:
    """Card queries, suspension, ease/state edits, reviews, and stats actions."""

    @util.api()
    def getNumCardsReviewedToday(self):
        return self.database().scalar(
            "select count() from revlog where id > ?",
            (self.scheduler().day_cutoff - 86400) * 1000,
        )

    @util.api()
    def getNumCardsReviewedByDay(self):
        return self.database().all(
            'select date(id/1000 - ?, "unixepoch", "localtime") as day, count() '
            "from revlog group by day order by day desc",
            int(time.strftime("%H", time.localtime(self.scheduler().day_cutoff))) * 3600,
        )

    @util.api()
    def getCollectionStatsHTML(self, wholeCollection=True):
        stats = self.collection().stats()
        stats.wholeCollection = wholeCollection
        return stats.report()

    @util.api()
    def reloadCollection(self):
        # Anki resets its queues after every operation, so there is nothing left to reload;
        # the call still fails when no collection is open.
        self.collection()

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
            return False
        if not isinstance(keys, list) or not isinstance(newValues, list):
            return False
        if len(newValues) != len(keys):
            return False

        for key in keys:
            if key in _SENSITIVE_CARD_KEYS and warning_check is False:
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
        seen = set()
        for card in cards:
            if type(card) is int and card in queues:
                isSuspended = queues[card] == -1
            else:
                isSuspended = self.suspended(card)
            if isSuspended != suspend and card not in seen:
                seen.add(card)
                to_change.append(card)

        if len(to_change) == 0:
            return False

        scheduler = self.scheduler()
        if suspend:
            scheduler.suspend_cards(to_change)
        else:
            scheduler.unsuspend_cards(to_change)
        self.scheduleRefresh()
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
    def findCards(self, query=None):
        if query is None:
            return []
        return list(map(int, self.collection().find_cards(query)))

    @util.api()
    def cardsInfo(self, cards):
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
                result.append({})

        return result

    @util.api()
    def cardsModTime(self, cards):
        result = []
        for cid in cards:
            try:
                card = self.getCard(cid)
                result.append({"cardId": card.id, "mod": card.mod})
            except NotFoundError:
                result.append({})
        return result

    @util.api()
    def cardsToNotes(self, cards):
        return self.collection().db.list(
            "select distinct nid from cards where id in " + anki.utils.ids2str(cards)
        )

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
        columns = ["id", "usn", "ease", "ivl", "lastIvl", "factor", "time", "type"]
        revlog_cols = {row[1] for row in db.all("pragma table_info(revlog)")}
        if "data" in revlog_cols:
            columns.append("data")
        col_list = ", ".join(columns)
        query = f"select {col_list} from revlog where cid = ?"

        # One query for every integer card id. revlog.id is its rowid, so the per-card
        # query above returns a card's rows in id order; the batch keeps that order.
        cardIds = {card for card in cards if type(card) is int}
        rows = {card: [] for card in cardIds}
        if cardIds:
            batch = (
                f"select cid, {col_list} from revlog "
                f"where cid in {anki.utils.ids2str(cardIds)} order by cid, id"
            )
            for row in db.all(batch):
                rows[row[0]].append(row[1:])

        result = {}
        for card in cards:
            if type(card) is int:
                query_result = rows[card]
            else:
                query_result = db.all(query, card)
            result[card] = [dict(zip(columns, row, strict=False)) for row in query_result]

        return result

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
            if not isinstance(row, list | tuple) or len(row) != 9:
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
