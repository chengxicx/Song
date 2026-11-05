"""
Persistent storage for AI sentence and grammar results.

The old ``ai_sentence_cache_v2_*`` setting entries were a TTL cache: they
expired and only lived in the per-user settings table.  Results are now
kept in this table, keyed by sentence + target language + provider entry +
model + result type, so the same sentence opening again is a row read and
never costs an upstream request.  Different models or target languages
produce different rows and coexist.
"""

from datetime import datetime

from lute.db import db


class AiResult(db.Model):  # pylint: disable=too-few-public-methods
    "One structured AI result."

    __tablename__ = "airesults"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(255), unique=True, nullable=False, index=True)
    result_type = db.Column(db.String(20), nullable=False, default="sentence")
    book_id = db.Column(db.Integer, index=True)
    lang_id = db.Column(db.Integer, index=True)
    sentence = db.Column(db.Text, nullable=False, default="")
    result_json = db.Column(db.Text, nullable=False, default="")
    provider_id = db.Column(db.String(64), nullable=False, default="")
    model = db.Column(db.String(255), nullable=False, default="")
    target_lang = db.Column(db.String(64), nullable=False, default="")
    source = db.Column(db.String(20), nullable=False, default="live")
    token_usage = db.Column(db.Text)
    hit_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )
