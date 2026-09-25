"""
Review queue entities: scheduled cards and review logs.
"""

from lute.db import db


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
    # The old RcSpecID column is left in the db but no longer mapped:
    # cards are auto-admitted, there are no specs.

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
