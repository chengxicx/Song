"""
Review queue entities: admission specs, scheduled cards, review logs.
"""

import json

from lute.db import db


class ReviewSpec(db.Model):
    """
    A rule admitting terms into the review queue.

    criteria is a lute.ankiexport.criteria DSL string, e.g.
    'status > 1 and language == "Japanese"'.
    """

    __tablename__ = "reviewspecs"

    id = db.Column("RsID", db.Integer, primary_key=True)
    name = db.Column("RsName", db.String(200), nullable=False, unique=True)
    criteria = db.Column("RsCriteria", db.String(1000), nullable=False, default="")
    card_types = db.Column(
        "RsCardTypes",
        db.String(200),
        nullable=False,
        default='{"recognition": 1, "recall": 0, "cloze": 1}',
    )
    active = db.Column("RsActive", db.Boolean, nullable=False, default=True)

    CARD_TYPES = ["recognition", "recall", "cloze"]

    # Column defaults only apply at insert time; an unflushed spec
    # must behave the same as a saved one.
    DEFAULT_CARD_TYPES = '{"recognition": 1, "recall": 0, "cloze": 1}'

    @property
    def card_types_enabled(self):
        "Card types turned on for this spec, in canonical order."
        raw = (
            self.card_types if self.card_types is not None else self.DEFAULT_CARD_TYPES
        )
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            d = {}
        return [ct for ct in self.CARD_TYPES if d.get(ct)]

    def set_card_types(self, enabled_list):
        "Set the enabled card types from a list."
        d = {ct: (ct in enabled_list) for ct in self.CARD_TYPES}
        self.card_types = json.dumps(d)


class ReviewCard(db.Model):
    """
    One scheduled card: a term in one card type, with FSRS state.
    """

    __tablename__ = "reviewcards"

    # 0 = new (not yet graded, Lute-side marker); 1-3 mirror fsrs.State
    # (1 Learning, 2 Review, 3 Relearning).
    STATE_NEW = 0

    id = db.Column("RcID", db.Integer, primary_key=True)
    term_id = db.Column(
        "RcWoID", db.Integer, db.ForeignKey("words.WoID"), nullable=False
    )
    card_type = db.Column("RcCardType", db.String(20), nullable=False)
    due = db.Column("RcDue", db.DateTime)
    state = db.Column("RcState", db.Integer, nullable=False, default=0)
    stability = db.Column("RcStability", db.Float)
    difficulty = db.Column("RcDifficulty", db.Float)
    reps = db.Column("RcReps", db.Integer, nullable=False, default=0)
    lapses = db.Column("RcLapses", db.Integer, nullable=False, default=0)
    last_review = db.Column("RcLastReview", db.DateTime)
    created = db.Column("RcCreated", db.DateTime)
    spec_id = db.Column("RcSpecID", db.Integer, db.ForeignKey("reviewspecs.RsID"))

    term = db.relationship("Term")

    __table_args__ = (
        db.UniqueConstraint("RcWoID", "RcCardType", name="uq_reviewcards_term_type"),
        db.Index("ix_reviewcards_due", "RcDue"),
    )

    def __repr__(self):
        return f"<ReviewCard {self.id} term {self.term_id} {self.card_type}>"


class ReviewLog(db.Model):
    "One grading event on a card."

    __tablename__ = "reviewlogs"

    id = db.Column("RlID", db.Integer, primary_key=True)
    card_id = db.Column(
        "RlRcID", db.Integer, db.ForeignKey("reviewcards.RcID"), nullable=False
    )
    review_time = db.Column("RlReviewTime", db.DateTime, nullable=False)
    rating = db.Column("RlRating", db.Integer, nullable=False)
    reps_before = db.Column("RlRepsBefore", db.Integer, nullable=False, default=0)
    data = db.Column("RlData", db.Text)
