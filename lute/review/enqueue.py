"""
Auto-admit learning terms into the review queue.

Every term in the learning statuses (1-5) gets a card per enabled card
type.  Admission is purely additive: cards already in the queue are
never touched or deleted, so changing the settings cannot destroy
scheduling history.  Cloze cards require the term to appear in at
least one read sentence (the card blanks the term inside it).
"""

import json
from datetime import datetime, timezone

from sqlalchemy import text

from lute.db import db
from lute.models.repositories import UserSettingRepository

# Learning-range statuses only: unknown (0), ignored (98) and
# well-known (99) terms are not review material.
_LEARNING_STATUSES = [1, 2, 3, 4, 5]

CARD_TYPES = ["recognition", "cloze"]
DEFAULT_CARD_TYPES = ["recognition", "cloze"]

_SETTING_KEY = "review_card_types"
_ZWS = chr(0x200B)

# Set-based admission: one INSERT ... SELECT per card type.  The
# unique (RcWoID, RcCardType) constraint plus OR IGNORE keeps repeats
# harmless.  Cloze needs the term inside a *read* sentence: sentences
# store the text with words wrapped in zero-width spaces, and SeTextLC
# is '*' when the language's LOWER() needed no parser help, in which
# case the original text is used.  INSTR rather than LIKE keeps % and
# _ in the term text literal.
_CLOZE_SENTENCE_EXISTS = """
  EXISTS (
    SELECT 1
    FROM sentences
    INNER JOIN texts ON texts.TxID = sentences.SeTxID
    INNER JOIN books ON books.BkID = texts.TxBkID
    WHERE texts.TxReadDate IS NOT NULL
      AND books.BkLgID = words.WoLgID
      AND sentences.SeText IS NOT NULL
      AND INSTR(
            CASE WHEN sentences.SeTextLC = '*' THEN sentences.SeText
                 ELSE sentences.SeTextLC END,
            :zws || words.WoTextLC || :zws
          ) > 0
  )
"""


def enabled_card_types(session):
    """
    Card types admitted by the global review_card_types setting, in
    canonical order.  A missing or broken setting means the defaults,
    never "no cards at all".
    """
    repo = UserSettingRepository(session)
    raw = repo.get_dynamic_value(_SETTING_KEY)
    if raw is None:
        return list(DEFAULT_CARD_TYPES)
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return list(DEFAULT_CARD_TYPES)
    if not isinstance(d, dict):
        return list(DEFAULT_CARD_TYPES)
    return [ct for ct in CARD_TYPES if d.get(ct)]


def set_enabled_card_types(session, enabled_list):
    "Persist the global card-type switch from a list of enabled types."
    d = {ct: (ct in enabled_list) for ct in CARD_TYPES}
    repo = UserSettingRepository(session)
    repo.set_dynamic_value(_SETTING_KEY, json.dumps(d))


def auto_admit(session):
    """
    Enqueue cards for all terms in the learning statuses.

    Returns the number of cards inserted, per card type, e.g.
    {"recognition": 3, "cloze": 1}.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    types = enabled_card_types(session)
    if not types:
        return {}

    statuses = ",".join(str(s) for s in _LEARNING_STATUSES)
    counts = {}
    for card_type in types:
        extras = ""
        params = {
            "ctype": card_type,
            "now": now,
            "zws": _ZWS,
        }
        if card_type == "cloze":
            extras = f" AND {_CLOZE_SENTENCE_EXISTS}"
        sql = text(
            f"""
            INSERT OR IGNORE INTO reviewcards
              (RcWoID, RcCardType, RcDue, RcState, RcReps, RcLapses, RcCreated)
            SELECT words.WoID, :ctype, :now, 0, 0, 0, :now
            FROM words
            WHERE words.WoStatus IN ({statuses}){extras}
            """
        )
        result = session.execute(sql, params)
        counts[card_type] = result.rowcount or 0

    session.commit()
    return counts
