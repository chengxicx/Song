"""
Storage for Eudic sync state: one row per (term, bucket, category).

Not to be confused with ``words.WoSyncStatus``, which is the "follow my
parent term's status" flag and is already used by the parent-term
triggers; reusing it would corrupt status propagation.
"""

from datetime import datetime, timezone

from sqlalchemy import text

from lute.db import db


def _now():
    "Naive UTC, matching the rest of the schema."
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EudicSync(db.Model):
    "Sync bookkeeping for one term in one Eudic studylist."

    __tablename__ = "eudicsync"

    id = db.Column("EsID", db.Integer, primary_key=True)
    term_id = db.Column(
        "EsWoID", db.Integer, db.ForeignKey("words.WoID"), nullable=False
    )
    language_id = db.Column(
        "EsLgID", db.Integer, db.ForeignKey("languages.LgID"), nullable=False
    )
    bucket = db.Column("EsBucket", db.String(2), nullable=False)
    category_id = db.Column("EsCategoryID", db.String(40), nullable=False, default="0")
    remote_word = db.Column("EsRemoteWord", db.String(250), nullable=False)
    word_key = db.Column("EsWordKey", db.String(250), nullable=False)
    remote_star = db.Column("EsRemoteStar", db.Integer)
    last_synced_star = db.Column(
        "EsLastSyncedStar", db.Integer, nullable=False, default=-1
    )
    last_synced_status = db.Column(
        "EsLastSyncedStatus", db.Integer, nullable=False, default=-1
    )
    remote_add_time = db.Column("EsRemoteAddTime", db.String(30))
    remote_present = db.Column("EsRemotePresent", db.Integer, nullable=False, default=0)
    last_pushed_at = db.Column("EsLastPushedAt", db.DateTime)
    last_pulled_at = db.Column("EsLastPulledAt", db.DateTime)
    last_error = db.Column("EsLastError", db.String(500))
    last_error_at = db.Column("EsLastErrorAt", db.DateTime)
    fail_count = db.Column("EsFailCount", db.Integer, nullable=False, default=0)
    created = db.Column("EsCreated", db.DateTime)
    updated = db.Column("EsUpdated", db.DateTime)


class EudicSyncRepository:
    "Queries and updates for the sync table."

    def __init__(self, session):
        self.session = session

    def for_scope(self, lg_id, bucket, category_id):
        """
        ``{term_id: EudicSync}`` for every tracked word in this scope.
        """
        rows = (
            self.session.query(EudicSync)
            .filter(
                EudicSync.language_id == lg_id,
                EudicSync.bucket == bucket,
                EudicSync.category_id == str(category_id),
            )
            .all()
        )
        return {r.term_id: r for r in rows}

    def get(self, term_id, bucket, category_id):
        "One row, or None."
        return (
            self.session.query(EudicSync)
            .filter(
                EudicSync.term_id == term_id,
                EudicSync.bucket == bucket,
                EudicSync.category_id == str(category_id),
            )
            .first()
        )

    def find_by_remote_word(self, lg_id, bucket, category_id, remote_word):
        "The row tracking this exact remote string, if any."
        return (
            self.session.query(EudicSync)
            .filter(
                EudicSync.language_id == lg_id,
                EudicSync.bucket == bucket,
                EudicSync.category_id == str(category_id),
                EudicSync.remote_word == remote_word,
            )
            .first()
        )

    def upsert(
        self,
        *,
        term_id,
        lg_id,
        bucket,
        category_id,
        remote_word,
        word_key,
        remote_star=None,
        synced_star=None,
        synced_status=None,
        remote_add_time=None,
        direction="push",
        remote_present=1,
    ):
        """
        Record a successful sync for one word.

        ``synced_star`` / ``synced_status`` are the values both sides now
        agree on; leaving them None keeps whatever baseline was stored,
        which is what a pull that only read a star wants.
        """
        row = self.get(term_id, bucket, category_id)
        now = _now()
        if row is None:
            row = EudicSync(
                term_id=term_id,
                language_id=lg_id,
                bucket=bucket,
                category_id=str(category_id),
                created=now,
            )
            self.session.add(row)
        row.remote_word = remote_word
        row.word_key = word_key
        row.remote_star = remote_star
        if synced_star is not None:
            row.last_synced_star = synced_star
        if synced_status is not None:
            row.last_synced_status = synced_status
        if remote_add_time is not None:
            row.remote_add_time = remote_add_time
        row.remote_present = remote_present
        if direction == "push":
            row.last_pushed_at = now
        else:
            row.last_pulled_at = now
        row.last_error = None
        row.last_error_at = None
        row.fail_count = 0
        row.updated = now
        return row

    def record_error(
        self, *, term_id, lg_id, bucket, category_id, remote_word, word_key, message
    ):
        "Remember a per-word failure without touching the sync baseline."
        row = self.get(term_id, bucket, category_id)
        now = _now()
        if row is None:
            # A word can fail before it ever synced; keep a stub so the
            # failure is visible on the page and retried next time.
            row = EudicSync(
                term_id=term_id,
                language_id=lg_id,
                bucket=bucket,
                category_id=str(category_id),
                remote_word=remote_word or "",
                word_key=word_key or "",
                created=now,
            )
            self.session.add(row)
        row.last_error = (message or "")[:500]
        row.last_error_at = now
        row.fail_count = (row.fail_count or 0) + 1
        row.updated = now
        return row

    def stats(self, lg_id, bucket, category_id):
        "Counters for the page header."
        sql = (
            "select count(*) as mapped, "
            "sum(case when EsRemotePresent = 1 then 1 else 0 end) as on_remote, "
            "sum(case when EsLastError is not null then 1 else 0 end) as errored, "
            "max(EsLastPushedAt) as last_pushed, "
            "max(EsLastPulledAt) as last_pulled "
            "from eudicsync "
            "where EsLgID = :lgid and EsBucket = :bucket "
            "and EsCategoryID = :cat"
        )
        row = self.session.execute(
            text(sql), {"lgid": lg_id, "bucket": bucket, "cat": str(category_id)}
        ).first()
        if row is None:
            return {
                "mapped": 0,
                "on_remote": 0,
                "errored": 0,
                "last_pushed": None,
                "last_pulled": None,
            }
        return {
            "mapped": int(row[0] or 0),
            "on_remote": int(row[1] or 0),
            "errored": int(row[2] or 0),
            "last_pushed": row[3],
            "last_pulled": row[4],
        }

    def recent_errors(self, lg_id, bucket, category_id, limit=50):
        "The last few failures, newest first."
        sql = (
            "select s.EsRemoteWord, s.EsLastError, s.EsLastErrorAt, w.WoText "
            "from eudicsync s left join words w on w.WoID = s.EsWoID "
            "where s.EsLgID = :lgid and s.EsBucket = :bucket "
            "and s.EsCategoryID = :cat and s.EsLastError is not null "
            "order by s.EsLastErrorAt desc limit :lim"
        )
        rows = self.session.execute(
            text(sql),
            {
                "lgid": lg_id,
                "bucket": bucket,
                "cat": str(category_id),
                "lim": int(limit),
            },
        ).all()
        return [
            {
                "word": r[3] or r[0] or "",
                "error": r[1] or "",
                "at": r[2],
            }
            for r in rows
        ]

    def clear_errors(self, lg_id, bucket, category_id):
        "Wipe remembered failures for a scope; returns the row count."
        sql = (
            "update eudicsync set EsLastError = null, EsLastErrorAt = null, "
            "EsFailCount = 0 where EsLgID = :lgid and EsBucket = :bucket "
            "and EsCategoryID = :cat"
        )
        result = self.session.execute(
            text(sql), {"lgid": lg_id, "bucket": bucket, "cat": str(category_id)}
        )
        return result.rowcount or 0


class EudicConfig(db.Model):
    """
    The current user's Eudic connection.

    Kept out of the generic settings table on purpose: every settings row
    is serialised into the LUTE_USER_SETTINGS javascript namespace on each
    page render, which would embed the credential blob in every response.
    """

    __tablename__ = "eudicconfig"

    id = db.Column("EcID", db.Integer, primary_key=True)
    api_key = db.Column("EcApiKey", db.Text, nullable=False, default="")
    scopes = db.Column("EcScopes", db.Text, nullable=False, default="{}")
    updated = db.Column("EcUpdated", db.DateTime)


class EudicConfigRepository:
    "Load and save the single config row."

    ROW_ID = 1

    def __init__(self, session):
        self.session = session

    def load(self):
        "The row, created empty if this is the first visit."
        row = self.session.get(EudicConfig, self.ROW_ID)
        if row is None:
            row = EudicConfig(id=self.ROW_ID, api_key="", scopes="{}")
            self.session.add(row)
            self.session.commit()
        return row

    def save(self, row):
        "Persist changes."
        row.updated = _now()
        self.session.add(row)
        self.session.commit()
