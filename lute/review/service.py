"""
Review session service: counts, session assembly, grading.

Card content follows the ankiexport field-mapping conventions: the
sentence comes from real read sentences via SentenceLookup, with the
term wrapped in <b></b>; cloze fronts blank those <b> spans.
"""

import re
import unicodedata
from datetime import datetime, time, timezone

from lute.ankiexport.field_mapping import SentenceLookup
from lute.models.repositories import UserSettingRepository
from lute.models.review import ReviewCard, ReviewLog
from lute.term.model import ReferencesRepository
from lute.review import scheduler

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
    return {
        "desired_retention": repo.get_value("review_desired_retention"),
        "max_new_per_day": max(max_new, 0),
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

    Raises SchedulerUnavailableError when the fsrs package is missing.
    """
    st = _settings(session)
    sched = scheduler.load_scheduler(st["desired_retention"])

    c = counts(session)
    now = _utcnow_naive()

    due_cards = (
        session.query(ReviewCard)
        .filter(ReviewCard.reps > 0, ReviewCard.due <= now)
        .order_by(ReviewCard.due)
        .limit(_MAX_DUE_CARDS)
        .all()
    )
    new_cards = []
    if c["new_allowed_today"] > 0:
        new_cards = (
            session.query(ReviewCard)
            .filter(ReviewCard.reps == 0, ReviewCard.due <= now)
            .order_by(ReviewCard.created, ReviewCard.id)
            .limit(c["new_allowed_today"])
            .all()
        )

    lookup = SentenceLookup({}, ReferencesRepository(session))
    now_aware = _utcnow_aware()
    cards = [
        _card_view(card, lookup, sched, now_aware) for card in due_cards + new_cards
    ]
    return {"counts": c, "cards": cards}


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
        "reps": dbcard.reps,
    }
    if dbcard.card_type == "cloze":
        view["sentence_blank"] = _cloze_front(sentence, term)
    fcard = scheduler.load_card(dbcard)
    view["intervals"] = scheduler.next_intervals(sched, fcard, now_aware)
    return view


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


def grade(session, card_id, rating_int, typed_answer=None):
    """
    Grade one card: check typed answers (recall/cloze), run FSRS,
    persist the new state and a review log.

    A wrong typed answer forces rating 1 (Again).  Returns a result
    dict; raises SchedulerUnavailableError when fsrs is missing.
    """
    card = session.get(ReviewCard, card_id)
    if card is None:
        raise ValueError(f"No review card with id {card_id}")
    if rating_int not in (1, 2, 3, 4):
        raise ValueError(f"Invalid rating {rating_int}")

    correct = True
    needs_typing = card.card_type in ("recall", "cloze")
    if needs_typing and (typed_answer or "").strip() != "":
        correct = _normalize_answer(typed_answer) == _normalize_answer(
            card.term.text or ""
        )
        if not correct:
            rating_int = 1

    st = _settings(session)
    sched = scheduler.load_scheduler(st["desired_retention"])

    now_aware = _utcnow_aware()
    fcard = scheduler.load_card(card)
    reps_before = card.reps
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
            data=scheduler.log_snapshot(flog),
        )
    )
    session.commit()

    return {
        "correct": correct,
        "answer": (card.term.text or "").replace(ZWS, ""),
        "state": int(new_fcard.state),
        "due": card.due.isoformat() if card.due else None,
    }
