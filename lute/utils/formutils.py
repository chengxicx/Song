"""
Common form methods.
"""

from lute.models.language import Language
from lute.models.repositories import UserSettingRepository
from lute.db import db


def language_choices(session, dummy_entry_placeholder="-", include_inactive=False):
    """
    Return the list of languages for select boxes.

    If only one lang exists, only return that,
    otherwise add a '-' dummy entry at the top.
    """
    query = session.query(Language).order_by(Language.name)
    if not include_inactive:
        query = query.filter(Language.is_active == True)  # noqa: E712
    langs = query.all()
    supported = [lang for lang in langs if lang.is_supported]
    lang_choices = [(s.id, s.name) for s in supported]
    # Add a dummy placeholder even if there are no languages.
    if len(lang_choices) != 1:
        lang_choices = [(0, dummy_entry_placeholder)] + lang_choices
    return lang_choices


def valid_current_language_id(session):
    """
    Get the current language id from UserSetting, ensuring
    it's still valid.  If not, change it.
    Only invalidates if the language doesn't exist or isn't active,
    allows inactive or unsupported parsers (user may just be installing dependencies).
    """
    repo = UserSettingRepository(session)
    try:
        current_language_id = repo.get_value("current_language_id")
    except Exception:  # pylint: disable=broad-exception-caught
        # Setting doesn't exist (e.g. restored from older version)
        current_language_id = None

    if current_language_id is None:
        valid_language_ids = [int(p[0]) for p in language_choices(session)]
        current_language_id = valid_language_ids[0] if valid_language_ids else 0
        try:
            repo.set_value("current_language_id", current_language_id)
            session.commit()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return current_language_id

    current_language_id = int(current_language_id)

    # Check if the language id actually exists and is active.
    # We only filter out languages that don't exist or are not active.
    # Even if the parser is not supported (parser dependencies missing),
    # keep the user's selection because they might be in the process
    # of installing dependencies and expect selecting to stick.
    lang = session.query(Language).filter(Language.id == current_language_id).first()
    if lang is not None and lang.is_active:
        return current_language_id

    # If the selected current_language_id is invalid, fall back to the first active language.
    valid_language_ids = [int(p[0]) for p in language_choices(session)]
    current_language_id = valid_language_ids[0] if valid_language_ids else 0
    try:
        repo.set_value("current_language_id", current_language_id)
        session.commit()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return current_language_id


def book_tag_choices(session):
    """
    All book tags with their book counts, for the series-tags setting
    and book tag dropdowns.
    """
    rows = session.execute(
        db.text(
            """
            SELECT t2.T2Text, COUNT(bt.BtBkID) AS n
            FROM tags2 t2
            LEFT OUTER JOIN booktags bt ON bt.BtT2ID = t2.T2ID
            GROUP BY t2.T2Text
            ORDER BY t2.T2Text COLLATE NOCASE
            """
        )
    ).fetchall()
    return [(r[0], f"{r[0]} ({r[1]} book{'s' if r[1] != 1 else ''})") for r in rows]
