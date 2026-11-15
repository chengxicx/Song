"""
SQL fragments counting each book's AI-explained sentences.

Shared by the home book listing (lute.book.datatables) and the series
overview (lute.book.series).  They live in a module of their own
because datatables imports series (configured_series_tags), so series
cannot import datatables back.
"""

# Sentences per book and how many of them already have an AI sentence
# explanation, aggregated in one grouped subquery so the listing never
# runs a query per row.
#
# The numerator matches airesults rows by sentence text, not book_id:
# live panel lookups store the sentence without a book id (the panel is
# opened from a dictionary URI that carries no book), while imports and
# prefetch do set it.  airesults.sentence is the cleaned form (no
# zero-width spaces, whitespace collapsed and trimmed, see
# ai.service.clean_sentence), whereas sentences.SeText is the
# zero-width-space-joined token form, hence the normalization on the
# join: like clean_sentence, every whitespace character -- the fullwidth
# U+3000 space CJK texts keep between words, tabs, newlines -- maps to a
# plain space before the trim, and two collapse passes fold pairs of
# spaces into one.  That covers whitespace runs up to four characters,
# which is every run in practice; clean_sentence collapses runs of any
# length, so a longer run would still stay unmatched.
SENTENCE_KEY_SQL = """
    TRIM(
        REPLACE(REPLACE(
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                s.SeText, char(8203), ''),
                char(12288), ' '),
                char(9), ' '),
                char(10), ' '),
                char(13), ' '),
            '  ', ' '),
        '  ', ' ')
    )
"""

SENTENCE_STATS_SQL = f"""
    LEFT OUTER JOIN (
        SELECT t.TxBkID AS BkID,
               COUNT(s.SeID) AS sentencecount,
               COUNT(DISTINCT CASE WHEN ar.sentence IS NULL
                                   THEN NULL ELSE s.SeID END) AS explainedcount
        FROM texts t
        INNER JOIN sentences s ON s.SeTxID = t.TxID
        LEFT OUTER JOIN (
            SELECT DISTINCT TRIM(sentence) AS sentence
            FROM airesults
            WHERE result_type = 'sentence'
        ) ar ON ar.sentence = {SENTENCE_KEY_SQL}
        GROUP BY t.TxBkID
    ) sentstats ON sentstats.BkID = b.BkID
"""

# Share of the book's sentences that have an AI explanation, 0-100.
AI_EXPLAIN_PERCENT_SQL = """
    case
      when COALESCE(sentstats.sentencecount, 0) <= 0 then 0
      else min(
        100,
        cast(
          COALESCE(sentstats.explainedcount, 0) * 100.0
            / sentstats.sentencecount as integer
        )
      )
    end
"""
