"""
Testing management functions.

"management" is for global actions like clearing out the db.
"""

import pytest
from datetime import datetime
from sqlalchemy import text
from lute.db import db
from lute.models.setting import UserSetting
from lute.models.repositories import UserSettingRepository
from lute.models.review import ReviewCard, ReviewLog
from lute.db.management import delete_all_data, add_default_user_settings
from tests.dbasserts import assert_record_count_equals
from tests.utils import add_terms


def test_wiping_db_clears_out_all_tables(app_context):
    """
    DB is wiped clean if requested ... settings are left!
    """
    old_user_settings = db.session.query(UserSetting).all()

    delete_all_data(db.session)
    tables = [
        "books",
        "bookstats",
        "booktags",
        "languages",
        "sentences",
        "tags",
        "tags2",
        "texts",
        "wordflashmessages",
        "wordimages",
        "wordparents",
        "words",
        "wordsread",
        "wordtags",
    ]
    for t in tables:
        assert_record_count_equals(t, 0, t)

    sql = "select * from settings where stkeytype='user'"
    assert_record_count_equals(sql, len(old_user_settings), "user settings remain")
    sql = "select * from settings where StKeyType = 'system'"
    assert_record_count_equals(sql, 0, "no system settings")


def test_can_get_backup_settings_when_db_is_wiped(app_context):
    "The backupsettings struct assumes certain things about the data."
    delete_all_data(db.session)
    repo = UserSettingRepository(db.session)
    bs = repo.get_backup_settings()
    assert bs.backup_enabled, "backup is back to being enabled"
    assert bs.backup_dir is not None, "default restored"


def test_wiping_db_clears_out_review_data(app_context, spanish):
    """
    Review cards and logs are data like any other, so "delete
    everything" has to include them.

    reviewcards references words without an ON DELETE clause, so a card
    left behind makes the language delete below fail outright with a
    foreign key error.  (The legacy reviewspecs table is wiped too,
    though nothing writes to it any more.)
    """
    # pylint: disable=unbalanced-tuple-unpacking
    [term] = add_terms(spanish, ["gato"])

    card = ReviewCard(term_id=term.id, card_type="recognition")
    db.session.add(card)
    db.session.commit()

    db.session.add(ReviewLog(card_id=card.id, review_time=datetime.now(), rating=3))
    db.session.commit()

    delete_all_data(db.session)

    for t in ("reviewcards", "reviewlogs"):
        assert_record_count_equals(t, 0, t)


@pytest.fixture(name="us_repo")
def fixture_usersetting_repo(app_context):
    "Repo"
    r = UserSettingRepository(db.session)
    return r


def test_user_settings_loaded_with_defaults(us_repo):
    "Called on load."
    db.session.execute(text("delete from settings"))
    db.session.commit()
    assert us_repo.key_exists("backup_dir") is False, "key removed"
    add_default_user_settings(db.session, "blah")
    assert us_repo.key_exists("backup_dir") is True, "key created"

    # Check defaults
    b = us_repo.get_backup_settings()
    assert b.backup_enabled is True
    assert b.backup_dir is not None
    assert b.backup_auto is True
    assert b.backup_warn is True
    assert b.backup_count == 5


def test_user_settings_load_leaves_existing_values(us_repo):
    "Called on load."
    us_repo.set_value("backup_count", 17)
    db.session.commit()
    assert us_repo.get_value("backup_count") == "17"
    add_default_user_settings(db.session, "blah")
    b = us_repo.get_backup_settings()
    assert b.backup_count == 17, "still 17"
