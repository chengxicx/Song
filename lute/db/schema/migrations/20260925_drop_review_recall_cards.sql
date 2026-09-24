-- Drop the recall (see-the-meaning, type-the-word) review cards.
-- The card type was removed: only recognition and cloze cards are
-- admitted now.  Their scheduling history goes with them; recognition
-- and cloze cards are untouched.  The reviewspecs table and the
-- reviewcards.RcSpecID column stay (harmless leftovers, no longer used).

DELETE FROM reviewlogs
WHERE RlRcID IN (
    SELECT RcID FROM reviewcards WHERE RcCardType = 'recall'
);

DELETE FROM reviewcards WHERE RcCardType = 'recall';
