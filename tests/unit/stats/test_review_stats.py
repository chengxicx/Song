"""
Review (FSRS) statistics tests.

The stats module reads the pre-review scheduling state out of the review
log's own JSON snapshot, so these tests build cards and logs directly
rather than grading through service.grade(): that keeps them independent
of the optional fsrs package, which is exactly the constraint
review_stats.py is written under.
"""

import json
from datetime import date, datetime, timedelta, timezone

from lute.db import db
from lute.models.review import ReviewCard, ReviewLog
from lute.stats.review_stats import FSRS_DECAY, FSRS_FACTOR, get_review_stats

from tests.utils import add_terms


def _utcnow():
    "Naive UTC now, how the db stores datetimes."
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ago(days=0.0, hours=0.0):
    "A naive-UTC datetime in the past."
    return _utcnow() - timedelta(days=days, hours=hours)


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _make_card(term, card_type="recognition", reps=0, state=0, due=None, **kwargs):
    "One scheduled card row."
    card = ReviewCard(
        term_id=term.id,
        card_type=card_type,
        due=due if due is not None else _utcnow(),
        state=state,
        reps=reps,
        lapses=kwargs.pop("lapses", 0),
        stability=kwargs.pop("stability", None),
        difficulty=kwargs.pop("difficulty", None),
        last_review=kwargs.pop("last_review", None),
        created=kwargs.pop("created", _utcnow()),
    )
    db.session.add(card)
    db.session.commit()
    return card


def _make_log(card, when, rating, reps_before=0, before=None):
    """
    One grading row.

    ``before`` is the pre-review scheduling snapshot; passing None
    simulates a grading recorded before undo existed, which has no
    snapshot at all.
    """
    data = None
    if before is not None:
        data = json.dumps({"before": before, "log": {}})
    db.session.add(
        ReviewLog(
            card_id=card.id,
            review_time=when,
            rating=rating,
            reps_before=reps_before,
            data=data,
        )
    )
    db.session.commit()


def _snapshot(due=None, stability=None, last_review=None, state=2, reps=1):
    "The 'before' dict as service.grade writes it."
    return {
        "due": _iso(due),
        "state": state,
        "stability": stability,
        "difficulty": None,
        "last_review": _iso(last_review),
        "reps": reps,
        "lapses": 0,
    }


def _one_term(language, text="gato"):
    return add_terms(language, [text])[0]


def test_no_reviews_yields_empty_stats(empty_db, spanish):
    "A queue with cards but no gradings reports zeroes, not an error."
    _make_card(_one_term(spanish))
    data = get_review_stats(db.session, None, "7days")
    assert data["has_data"] is False
    assert data["summary"]["reviews"] == 0
    assert data["summary"]["on_time_rate"] is None
    assert data["summary"]["retention"] is None
    assert data["forgetting"]["buckets"] == []
    assert data["ratings"]["total"] == 0


def test_invalid_period_falls_back_to_7days(empty_db, spanish):
    "A junk period never reaches the query."
    data = get_review_stats(db.session, None, "nonsense")
    assert data["period"] == "7days"


def test_on_time_and_late_are_split(empty_db, spanish):
    """
    A card graded inside the grace window is on time; one graded two
    days after falling due is late.
    """
    term = _one_term(spanish)
    card = _make_card(term, reps=1, state=2)

    due_on_time = _ago(hours=2)
    _make_log(
        card,
        _ago(hours=1),
        3,
        reps_before=1,
        before=_snapshot(due=due_on_time, last_review=_ago(days=5)),
    )
    # Same card, graded well after its due date.
    due_late = _ago(days=4)
    _make_log(
        card,
        _ago(days=2),
        3,
        reps_before=2,
        before=_snapshot(due=due_late, last_review=_ago(days=4)),
    )

    s = get_review_stats(db.session, None, "7days")["summary"]
    assert s["reviews"] == 2
    assert s["on_time"] == 1
    assert s["late"] == 1
    assert s["on_time_rate"] == 0.5
    assert s["no_timing"] == 0


def test_reviewing_early_counts_as_on_time(empty_db, spanish):
    "A negative delay (graded before the due date) is not lateness."
    card = _make_card(_one_term(spanish), reps=1, state=2)
    _make_log(
        card,
        _ago(hours=1),
        3,
        reps_before=1,
        before=_snapshot(due=_ago(hours=-20), last_review=_ago(days=3)),
    )
    s = get_review_stats(db.session, None, "7days")["summary"]
    assert s["on_time"] == 1
    assert s["late"] == 0


def test_logs_without_a_snapshot_are_volume_only(empty_db, spanish):
    """
    Gradings recorded before undo existed have no 'before' snapshot:
    they count towards volume and retention but cannot be timed.
    """
    card = _make_card(_one_term(spanish), reps=2, state=2)
    _make_log(card, _ago(hours=1), 1, reps_before=2, before=None)

    data = get_review_stats(db.session, None, "7days")
    s = data["summary"]
    assert s["reviews"] == 1
    assert s["no_timing"] == 1
    assert s["on_time"] == 0
    assert s["on_time_rate"] is None
    assert s["retention"] == 0.0
    assert data["forgetting"]["buckets"] == []


def test_retention_counts_only_again_as_a_failure(empty_db, spanish):
    "Hard/Good/Easy all count as recalled; only Again is a miss."
    card = _make_card(_one_term(spanish), reps=4, state=2)
    for rating in (1, 2, 3, 4):
        _make_log(
            card,
            _ago(hours=rating),
            rating,
            reps_before=1,
            before=_snapshot(due=_ago(hours=rating + 1), last_review=_ago(days=2)),
        )

    data = get_review_stats(db.session, None, "7days")
    assert data["summary"]["recalled"] == 3
    assert data["summary"]["again"] == 1
    assert data["summary"]["retention"] == 0.75
    assert data["ratings"] == {
        "again": 1,
        "hard": 1,
        "good": 1,
        "easy": 1,
        "total": 4,
    }


def test_broken_snapshot_json_does_not_raise(empty_db, spanish):
    "A corrupt RlData row degrades to 'no timing data'."
    card = _make_card(_one_term(spanish), reps=1, state=2)
    db.session.add(
        ReviewLog(
            card_id=card.id,
            review_time=_ago(hours=1),
            rating=3,
            reps_before=1,
            data="{not json",
        )
    )
    db.session.commit()

    s = get_review_stats(db.session, None, "7days")["summary"]
    assert s["reviews"] == 1
    assert s["no_timing"] == 1
    assert s["retention"] == 1.0


def test_forgetting_curve_buckets_and_prediction(empty_db, spanish):
    """
    Reviews land in the elapsed-day bucket that matches their interval,
    and the predicted series is the FSRS-6 retrievability at that
    moment.
    """
    card = _make_card(_one_term(spanish), reps=3, state=2)

    # One review 0.5 days after the previous one, one exactly 10 days
    # after: the intervals are derived from the review time so the
    # elapsed days are exact, not "10 days minus the clock skew".
    near_review = _ago(hours=12)
    _make_log(
        card,
        near_review,
        3,
        reps_before=1,
        before=_snapshot(
            due=_ago(days=1),
            stability=5.0,
            last_review=near_review - timedelta(hours=12),
        ),
    )
    far_review = _ago(hours=1)
    _make_log(
        card,
        far_review,
        1,
        reps_before=2,
        before=_snapshot(
            due=_ago(days=1),
            stability=20.0,
            last_review=far_review - timedelta(days=10),
        ),
    )

    buckets = get_review_stats(db.session, None, "7days")["forgetting"]["buckets"]
    by_label = {b["label"]: b for b in buckets}
    assert set(by_label) == {"≤1d", "7-14d"}

    near = by_label["≤1d"]
    assert near["reviews"] == 1
    assert near["retention"] == 1.0

    far = by_label["7-14d"]
    assert far["reviews"] == 1
    assert far["retention"] == 0.0

    # 10 elapsed days against stability 20: R = (1 + FACTOR*0.5) ** DECAY.
    expected = (1.0 + FSRS_FACTOR * 10.0 / 20.0) ** FSRS_DECAY
    assert abs(far["predicted"] - expected) < 1e-9
    assert 0.85 < expected < 1.0, "sanity: the prediction is a probability"


def test_predicted_retention_matches_the_fsrs_formula():
    """
    Pin the prediction: at t == S the retrievability must be exactly
    the desired retention (0.9), which is what ties FSRS_DECAY and
    FSRS_FACTOR to the scheduler's own constants.
    """
    # pylint: disable=protected-access
    from lute.stats.review_stats import _predicted_retention

    assert abs(_predicted_retention(10.0, 10.0) - 0.9) < 1e-12
    assert _predicted_retention(10.0, 20.0) > _predicted_retention(20.0, 20.0)
    assert _predicted_retention(None, 10.0) is None
    assert _predicted_retention(10.0, None) is None
    assert _predicted_retention(10.0, 0) is None


def test_curve_ignores_first_reviews(empty_db, spanish):
    "A card's first grading has no interval to plot."
    card = _make_card(_one_term(spanish), reps=1, state=1)
    _make_log(
        card,
        _ago(hours=1),
        3,
        reps_before=0,
        before=_snapshot(due=_ago(hours=2), state=0, reps=0),
    )
    data = get_review_stats(db.session, None, "7days")
    assert data["summary"]["reviews"] == 1
    assert data["forgetting"]["buckets"] == []


def test_retention_by_state_and_card_type(empty_db, spanish):
    "Retention is broken down by the pre-review state and the card type."
    term = _one_term(spanish)
    learning = _make_card(term, "recognition", reps=1, state=1)
    review = _make_card(term, "cloze", reps=3, state=2)

    _make_log(learning, _ago(hours=3), 1, reps_before=1, before=_snapshot(state=1))
    _make_log(review, _ago(hours=2), 3, reps_before=3, before=_snapshot(state=2))
    _make_log(review, _ago(hours=1), 3, reps_before=4, before=_snapshot(state=2))

    data = get_review_stats(db.session, None, "7days")
    states = {g["key"]: g for g in data["by_state"]}
    assert states["state_1"]["reviews"] == 1
    assert states["state_1"]["retention"] == 0.0
    assert states["state_2"]["reviews"] == 2
    assert states["state_2"]["retention"] == 1.0

    types = {g["key"]: g for g in data["by_card_type"]}
    assert types["recognition"]["reviews"] == 1
    assert types["cloze"]["reviews"] == 2


def test_language_filter(empty_db, spanish, english):
    "Only the requested language's cards are counted."
    es_card = _make_card(_one_term(spanish, "gato"), reps=1, state=2)
    en_card = _make_card(_one_term(english, "cat"), reps=1, state=2)
    _make_log(es_card, _ago(hours=1), 3, reps_before=1, before=_snapshot())
    _make_log(en_card, _ago(hours=1), 1, reps_before=1, before=_snapshot())

    assert get_review_stats(db.session, None, "7days")["summary"]["reviews"] == 2
    es = get_review_stats(db.session, spanish.id, "7days")["summary"]
    assert es["reviews"] == 1
    assert es["retention"] == 1.0
    assert es["cards_total"] == 1


def test_period_windows(empty_db, spanish):
    "today/7days/monthly slice the daily series; a two-year-old review drops out."
    card = _make_card(_one_term(spanish), reps=2, state=2)
    for when in (_ago(hours=1), _ago(days=3), _ago(days=8), _ago(days=400)):
        _make_log(card, when, 3, reps_before=1, before=_snapshot())

    assert get_review_stats(db.session, None, "today")["summary"]["reviews"] == 1
    assert get_review_stats(db.session, None, "7days")["summary"]["reviews"] == 2
    assert get_review_stats(db.session, None, "monthly")["summary"]["reviews"] == 3
    # Every window still reports the all-time total.
    assert (
        get_review_stats(db.session, None, "today")["summary"]["reviews_all_time"] == 4
    )


def test_monthly_series_is_aggregated_by_calendar_month(empty_db, spanish):
    "The monthly chart buckets by month, not by day."
    card = _make_card(_one_term(spanish), reps=2, state=2)
    _make_log(card, _ago(hours=1), 3, reps_before=1, before=_snapshot())
    _make_log(card, _ago(days=1), 3, reps_before=1, before=_snapshot())

    series = get_review_stats(db.session, None, "monthly")["daily"]
    this_month = date.today().replace(day=1).isoformat()
    assert series == [
        {
            "date": this_month,
            "total": 2,
            "on_time": 0,
            "late": 0,
            "recalled": 2,
            "again": 0,
            "hard": 0,
            "good": 2,
            "easy": 0,
        }
    ]


def test_card_counts_report_the_backlog(empty_db, spanish):
    "Due-now, new and lapses come from the card table, not the logs."
    terms = add_terms(spanish, ["gato", "perro", "pajaro"])
    _make_card(terms[0], "recognition", reps=2, state=2, due=_ago(days=1), lapses=3)
    _make_card(terms[1], "recognition", reps=1, state=1, due=_ago(hours=-2))
    _make_card(terms[2], "recognition", reps=0, state=0, due=_ago(days=1))

    cards = get_review_stats(db.session, None, "7days")["summary"]
    assert cards["cards_total"] == 3
    assert cards["cards_due_now"] == 1
    assert cards["cards_new"] == 1
    assert cards["lapses"] == 3


def test_on_time_rate_is_none_when_nothing_can_be_timed(empty_db, spanish):
    "No timing data means no rate, rather than a misleading 0%."
    card = _make_card(_one_term(spanish), reps=1, state=2)
    _make_log(card, _ago(hours=1), 3, reps_before=1, before=None)
    s = get_review_stats(db.session, None, "7days")["summary"]
    assert s["on_time_rate"] is None
    assert s["avg_delay_days"] is None


def test_avg_delay_days_is_reported(empty_db, spanish):
    "The mean lateness is the mean of the per-review delays."
    card = _make_card(_one_term(spanish), reps=2, state=2)
    _make_log(
        card,
        _ago(hours=1),
        3,
        reps_before=1,
        before=_snapshot(due=_ago(hours=25), last_review=_ago(days=2)),
    )
    _make_log(
        card,
        _ago(hours=2),
        3,
        reps_before=2,
        before=_snapshot(due=_ago(hours=26), last_review=_ago(days=2)),
    )
    s = get_review_stats(db.session, None, "7days")["summary"]
    assert abs(s["avg_delay_days"] - 1.0) < 0.01
