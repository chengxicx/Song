"""
Review session service: counts, session assembly, grading.

Card content follows the ankiexport field-mapping conventions: the
sentence comes from real read sentences via SentenceLookup, with the
term wrapped in <b></b>; cloze fronts blank those <b> spans.
"""

import json
import re
import unicodedata
from datetime import datetime, time, timezone

from lute.ankiexport.field_mapping import SentenceLookup
from lute.models.repositories import UserSettingRepository
from lute.models.review import ReviewCard, ReviewLog
from lute.read import shadowing
from lute.term.model import ReferencesRepository
from lute.review import enqueue, scheduler
from lute.tts.routes import get_lang_code_for

ZWS = "\u200B"

# Cards pulled into a session, due cards first.
_MAX_DUE_CARDS = 200


def _utcnow_naive():
    "Now as naive UTC (how datetimes are stored)."
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utcnow_aware():
    "Now as UTC-aware (what fsrs requires)."
    return datetime.now(timezone.utc)


def _settings(session):
    "Review settings, defensively parsed."
    repo = UserSettingRepository(session)
    try:
        max_new = int(repo.get_value("review_max_new_per_day") or 20)
    except (TypeError, ValueError):
        max_new = 20
    try:
        max_shadowing = int(repo.get_value("review_max_shadowing_per_day") or 10)
    except (TypeError, ValueError):
        max_shadowing = 10
    return {
        "desired_retention": repo.get_value("review_desired_retention"),
        "max_new_per_day": max(max_new, 0),
        # Shadowing takes run 20-30s each -- an order of magnitude slower
        # than a recognition card -- so they get their own daily cap on
        # top of the global new-card limit.
        "max_shadowing_per_day": max(max_shadowing, 0),
    }


def _new_shown_today(session, today_start):
    "New cards already presented today (graded from reps==0)."
    return (
        session.query(ReviewLog)
        .filter(
            ReviewLog.review_time >= today_start,
            ReviewLog.reps_before == 0,
        )
        .count()
    )


def _shadowing_shown_today(session, today_start):
    "New shadowing cards already presented today."
    return (
        session.query(ReviewLog)
        .join(ReviewCard, ReviewCard.id == ReviewLog.card_id)
        .filter(
            ReviewLog.review_time >= today_start,
            ReviewLog.reps_before == 0,
            ReviewCard.card_type == "shadowing",
        )
        .count()
    )


def counts(session):
    "Queue counts for the index page and session start."
    now = _utcnow_naive()
    today_start = datetime.combine(_utcnow_naive().date(), time.min)
    max_new = _settings(session)["max_new_per_day"]
    new_shown = _new_shown_today(session, today_start)
    return {
        "due": session.query(ReviewCard)
        .filter(ReviewCard.reps > 0, ReviewCard.due <= now)
        .count(),
        "new_remaining": session.query(ReviewCard)
        .filter(ReviewCard.reps == 0, ReviewCard.due <= now)
        .count(),
        "new_allowed_today": max(0, max_new - new_shown),
        "max_new_per_day": max_new,
    }


def start_session(session):
    """
    Build the review session: due cards then new cards.

    Cards are auto-admitted first, so learning terms are always in the
    queue when a session starts.

    Shadowing cards are capped per day (they take tens of seconds each,
    not seconds); the cap applies to due and new shadowing cards alike.

    Raises SchedulerUnavailableError when the fsrs package is missing.
    """
    enqueue.auto_admit(session)

    st = _settings(session)
    sched = scheduler.load_scheduler(st["desired_retention"])

    c = counts(session)
    now = _utcnow_naive()
    today_start = datetime.combine(now.date(), time.min)
    shadow_room = max(
        0, st["max_shadowing_per_day"] - _shadowing_shown_today(session, today_start)
    )

    due_regular = (
        session.query(ReviewCard)
        .filter(
            ReviewCard.reps > 0,
            ReviewCard.due <= now,
            ReviewCard.card_type != "shadowing",
        )
        .order_by(ReviewCard.due)
        .limit(_MAX_DUE_CARDS)
        .all()
    )
    due_shadowing = []
    if shadow_room > 0:
        due_shadowing = (
            session.query(ReviewCard)
            .filter(
                ReviewCard.reps > 0,
                ReviewCard.due <= now,
                ReviewCard.card_type == "shadowing",
            )
            .order_by(ReviewCard.due)
            .limit(shadow_room)
            .all()
        )
    # One session, one due-date order, whatever the card type.
    due_cards = sorted(
        due_regular + due_shadowing,
        key=lambda card: (card.due or now, card.id),
    )[:_MAX_DUE_CARDS]

    new_cards = []
    if c["new_allowed_today"] > 0:
        new_regular = (
            session.query(ReviewCard)
            .filter(
                ReviewCard.reps == 0,
                ReviewCard.due <= now,
                ReviewCard.card_type != "shadowing",
            )
            .order_by(ReviewCard.created, ReviewCard.id)
            .limit(c["new_allowed_today"])
            .all()
        )
        new_shadowing = []
        if shadow_room > 0:
            new_shadowing = (
                session.query(ReviewCard)
                .filter(
                    ReviewCard.reps == 0,
                    ReviewCard.due <= now,
                    ReviewCard.card_type == "shadowing",
                )
                .order_by(ReviewCard.created, ReviewCard.id)
                .limit(min(c["new_allowed_today"], shadow_room))
                .all()
            )
        # The global daily-new cap still rules the mix; the shadowing
        # subset is already bounded by shadow_room.
        new_cards = sorted(
            new_regular + new_shadowing,
            key=lambda card: (card.created or now, card.id),
        )[: c["new_allowed_today"]]

    lookup = SentenceLookup({}, ReferencesRepository(session))
    now_aware = _utcnow_aware()
    cards = [
        _card_view(card, lookup, sched, now_aware) for card in due_cards + new_cards
    ]
    return {"counts": c, "cards": cards, "undo": undo_info(session)}


def _card_view(dbcard, lookup, sched, now_aware):
    "One card's display payload."
    term = dbcard.term
    sentence = (lookup.get_sentence_for_term(term.id) or "").replace(ZWS, "")
    sentence = sentence.replace("¶", "").strip()
    view = {
        "id": dbcard.id,
        "card_type": dbcard.card_type,
        "term_text": (term.text or "").replace(ZWS, ""),
        "translation": term.translation or "",
        "romanization": term.romanization or "",
        "sentence": sentence,
        "image": _image_src(term),
        # BCP-47 tag for speaking this card's term (the page pronounces
        # it with TTS).  Per card, not per session: one review queue
        # holds the terms of every language the user studies, and
        # tts.js's own detection is for the reader's single book.
        "lang_code": get_lang_code_for(term.language),
        "language_id": term.language_id,
        "reps": dbcard.reps,
    }
    if dbcard.card_type == "cloze":
        view["sentence_blank"] = _cloze_front(sentence, term)
    if dbcard.card_type == "shadowing":
        # The shadowing card has no reading page to collect word spans
        # from, so the server tokenizes the sentence into the same token
        # space the diff scores (and the client renders it as spans).
        plain = _plain_text(sentence)
        view["sentence_plain"] = plain
        view["sentence_tokens"] = shadowing.tokenize_for_diff(plain, term.language)
        # SenseVoice first, whisper as the fallback: when neither can
        # run, the card degrades to read-and-self-grade.
        view["transcribe_available"] = shadowing.transcription_available(term.language)
    fcard = scheduler.load_card(dbcard)
    view["intervals"] = scheduler.preview_intervals(sched, fcard, now_aware)
    return view


def _plain_text(sentence_html):
    "The sentence without its <b></b> markup, for speaking and scoring."
    return re.sub(r"<[^>]+>", "", sentence_html or "").strip()


def _image_src(term):
    "User image URL for the term, or None."
    img = term.get_current_image()
    if not img:
        return None
    return f"/userimages/{term.language_id}/{img}"


def _cloze_front(sentence, term):
    "Blank the term inside the sentence."
    if "<b>" in sentence:
        return re.sub(
            r"<b>(.*?)</b>", '<span class="cloze-blank">[...]</span>', sentence
        )
    term_lc = (term.text_lc or "").replace(ZWS, "")
    if term_lc:
        return re.sub(
            re.escape(term_lc),
            '<span class="cloze-blank">[...]</span>',
            sentence,
            flags=re.IGNORECASE,
        )
    return sentence


def _normalize_answer(s):
    "Typed-answer normalization: NFC, zws gone, whitespace collapsed."
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace(ZWS, "").replace("¶", "")
    return re.sub(r"\s+", " ", s).strip().casefold()


def grade(session, card_id, rating_int, typed_answer=None, shadowing_score=None):
    """
    Grade one card: check typed answers (recall/cloze), run FSRS,
    persist the new state and a review log.

    A wrong typed answer forces rating 1 (Again).  shadowing_score (the
    take's 0-100, when the client scored one) is recorded on the log
    for the stats page; it only reports, it never overrides the user's
    rating -- an ASR mishear must not punish the schedule.  Returns a
    result dict; raises SchedulerUnavailableError when fsrs is missing.
    """
    card = session.get(ReviewCard, card_id)
    if card is None:
        raise ValueError(f"No review card with id {card_id}")
    if rating_int not in (1, 2, 3, 4):
        raise ValueError(f"Invalid rating {rating_int}")

    correct = True
    if card.card_type == "cloze" and (typed_answer or "").strip() != "":
        correct = _normalize_answer(typed_answer) == _normalize_answer(
            card.term.text or ""
        )
        if not correct:
            rating_int = 1

    extra = {}
    if shadowing_score is not None:
        try:
            sval = int(shadowing_score)
            if 0 <= sval <= 100:
                extra["shadowing_score"] = sval
        except (TypeError, ValueError):
            pass

    st = _settings(session)
    sched = scheduler.load_scheduler(st["desired_retention"])

    now_aware = _utcnow_aware()
    fcard = scheduler.load_card(card)
    reps_before = card.reps
    # The pre-review state has to be recorded here: fsrs cannot work
    # backwards from the new state, so undo would otherwise be lossy.
    state_before = scheduler.card_state(card)
    new_fcard, flog = sched.review_card(
        fcard, scheduler.rating_value(rating_int), now_aware
    )
    scheduler.save_card(card, new_fcard, rating_int)

    session.add(
        ReviewLog(
            card_id=card.id,
            review_time=now_aware.replace(tzinfo=None),
            rating=rating_int,
            reps_before=reps_before,
            data=json.dumps(
                {"before": state_before, "log": scheduler.log_snapshot(flog), **extra}
            ),
        )
    )
    session.commit()

    return {
        "correct": correct,
        "answer": (card.term.text or "").replace(ZWS, ""),
        "state": int(new_fcard.state),
        "due": card.due.isoformat() if card.due else None,
        "undo": undo_info(session),
    }


def _last_log(session):
    "The most recent grading, or None."
    return session.query(ReviewLog).order_by(ReviewLog.id.desc()).first()


def _log_payload(log):
    "The RlData envelope as a dict, tolerating old or broken rows."
    try:
        payload = json.loads(log.data or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def undo_info(session):
    """
    What an undo would reverse, or None when there is nothing to undo.

    Gradings recorded before undo existed have no 'before' snapshot
    and are reported as not undoable.
    """
    log = _last_log(session)
    if log is None:
        return None
    if not _log_payload(log).get("before"):
        return None
    card = session.get(ReviewCard, log.card_id)
    if card is None:
        return None
    return {
        "card_id": card.id,
        "card_type": card.card_type,
        "term_text": (card.term.text or "").replace(ZWS, "") if card.term else "",
        "rating": log.rating,
    }


def undo_last(session):
    """
    Reverse the most recent grading: restore the card's scheduling
    state and drop the log row, so today's new-card count goes back too.

    Raises ValueError when there is nothing that can be undone.
    """
    log = _last_log(session)
    if log is None:
        raise ValueError("There is nothing to undo.")
    before = _log_payload(log).get("before")
    if not before:
        raise ValueError(
            "That grading was recorded before undo was supported, so it "
            "cannot be reversed."
        )
    card = session.get(ReviewCard, log.card_id)
    if card is None:
        raise ValueError("The graded card no longer exists.")

    # Read everything off the log before deleting it: after the commit
    # the instance is gone and its attributes are no longer loadable.
    rating = log.rating
    scheduler.restore_card(card, before)
    session.delete(log)
    session.commit()
    return {
        "card_id": card.id,
        "card_type": card.card_type,
        "term_text": (card.term.text or "").replace(ZWS, "") if card.term else "",
        "rating": rating,
        "undo": undo_info(session),
    }
