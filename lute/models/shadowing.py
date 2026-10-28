"""
Shadowing (read-aloud) attempts: one row per scored take.

Takes are scored in lute.read.shadowing's background tasks, from the
reading page and from shadowing review cards alike; the rows feed the
stats page and give the practice a history.
"""

from lute.db import db


class ShadowAttempt(db.Model):
    "One scored shadowing take of one sentence."

    __tablename__ = "shadowattempts"

    id = db.Column("SaID", db.Integer, primary_key=True)
    language_id = db.Column(
        "SaLgID", db.Integer, db.ForeignKey("languages.LgID"), nullable=False
    )
    book_id = db.Column("SaBkID", db.Integer, db.ForeignKey("books.BkID"))
    # Where the take came from: "read" (the reading page) or "review"
    # (a shadowing review card).
    source = db.Column("SaSource", db.String(20), nullable=False, default="read")
    sentence = db.Column("SaSentence", db.Text)
    score = db.Column("SaScore", db.Integer, nullable=False, default=0)
    duration = db.Column("SaDuration", db.Float)
    tokens_per_min = db.Column("SaTokensPerMin", db.Float)
    engine = db.Column("SaEngine", db.String(20))
    # JSON: the sentence's tokens with their per-word verdicts,
    # [{"text": ..., "status": 0-3}, ...] -- the STATUS_* values from
    # lute.read.shadowing.
    tokens = db.Column("SaTokens", db.Text)
    created = db.Column("SaCreated", db.DateTime)

    language = db.relationship("Language")
    book = db.relationship("Book")

    def __repr__(self):
        return f"<ShadowAttempt {self.id} lang {self.language_id} score {self.score}>"
