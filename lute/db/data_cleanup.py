"""
Data cleanup routines.

Sometimes required as data management changes.
These cleanup routines will be called by the app_factory.
"""

from sqlalchemy import select, text as sqltext
from lute.models.language import Language
from lute.models.book import Book, Text, Sentence
from lute.models.term import TermImage
from lute.models.repositories import UserSettingRepository

# User setting marking that the one-time pdf page word-count backfill
# has run (see _set_pdf_texts_word_count).
_PDF_WC_BACKFILL_FLAG = "pdf_page_word_counts_backfilled"


class ProgressReporter:
    "Report progress for to an output function."

    def __init__(self, total_count, output_func, report_every=100):
        "Setup counters."
        self.current = 0
        self.last_output = 0
        self.total_count = total_count
        self.report_every = report_every
        self.output_func = output_func

    def increment(self):
        "Increment counter, and if past threshold, output."
        if self.total_count == 0:
            return
        self.current += 1
        if self.current - self.last_output < self.report_every:
            return
        self.output_func(f"  {self.current} of {self.total_count}")
        self.last_output = self.current


def _set_pdf_texts_word_count(session, output_function):
    """
    One-time backfill of pdf books' page word counts.

    Pdf books store one empty text per PDF page, so counts cannot be
    derived from the page text.  Books imported before per-page counts
    existed have all-zero counts: their empty texts were zeroed by
    _set_texts_word_count on earlier startups.  Recompute every pdf
    book's counts from the stored PDF file.

    The user-setting flag marks completion.  Books whose language has
    no working parser are skipped, and the flag stays unset so they
    are retried on the next startup after the parser is fixed.
    """
    from lute.book.service import Service as BookService  # pylint: disable=import-outside-toplevel

    repo = UserSettingRepository(session)
    if (repo.get_dynamic_value(_PDF_WC_BACKFILL_FLAG) or "") == "1":
        return

    books = session.query(Book).filter(Book.book_type == "pdf").all()
    if not books:
        repo.set_dynamic_value(_PDF_WC_BACKFILL_FLAG, "1")
        session.commit()
        return

    output_function(f"Fixing word counts for {len(books)} PDF books.")
    pr = ProgressReporter(len(books), output_function)
    svc = BookService()
    skipped = 0
    for b in books:
        if b.language is None or not b.language.is_supported:
            skipped += 1
            continue
        try:
            svc.set_pdf_page_word_counts(b, force=True)
            session.add(b)
            pr.increment()
        except Exception as e:  # pylint: disable=broad-exception-caught
            skipped += 1
            output_function(f"  Could not extract text from PDF book '{b.title}': {e}")
    session.commit()
    if skipped == 0:
        repo.set_dynamic_value(_PDF_WC_BACKFILL_FLAG, "1")
        session.commit()
    output_function("Done.")


def _set_texts_word_count(session, output_function):
    """
    texts.TxWordCount should be set for all texts.

    Fixing a design error: the counts should have been stored here,
    instead of only in books.BkWordCount.

    Ref https://github.com/jzohrab/lute-v3/issues/95
    """
    calc_counts = session.query(Text).filter(Text.word_count.is_(None)).all()

    # Don't recalc with invalid parsers!!!!
    recalc = [t for t in calc_counts if t.book.language.is_supported]

    if len(recalc) == 0:
        # Nothing to calculate, quit.
        return

    output_function(f"Fixing word counts for {len(recalc)} Texts.")
    pr = ProgressReporter(len(recalc), output_function)
    for t in recalc:
        pr.increment()
        pt = t.book.language.get_parsed_tokens(t.text)
        words = [w for w in pt if w.is_word]
        t.word_count = len(words)
        session.add(t)
    session.commit()
    output_function("Done.")


def _load_sentence_textlc(session, output_function):
    """
    sentences.SeTextLC was added after deployment, need to load it
    to fix issue 531.  ref https://github.com/LuteOrg/lute-v3/issues/531

    Only update sentences where the language is supported.  e.g. the
    user may have installed mecab, done some japanese, and then
    uninstalled mecab: the data will be hidden, but it's still
    present, and the sentences cannot be updated as the parser can't
    be loaded.
    """

    supported_langs = {
        lang.id: lang for lang in session.query(Language).all() if lang.is_supported
    }
    langids = [f"{k}" for k in list(supported_langs.keys())]
    if len(langids) == 0:
        langids = ["-999"]  # dummy to ensure good base sql

    base_sql = f"""
    select SeID, BkLgID
    from sentences
    inner join texts on SeTxID = TxID
    inner join books on BkID = TxBkID
    where BkLgID in ({','.join(langids)})
    and SeTextLC is null
    """

    count = session.execute(sqltext(f"select count(*) from ({base_sql}) src")).scalar()
    if count == 0:
        # Do nothing, don't print messages."
        return

    def _get_next_batch(batch_size):
        # Query for up to 1000 Sentence objects where textlc_content is None
        sql = f"{base_sql} limit {batch_size}"
        recs = session.execute(sqltext(sql)).all()
        seids = [int(rec[0]) for rec in recs]
        if len(seids) == 0:
            return []

        sentences = session.query(Sentence).filter(Sentence.id.in_(seids)).all()
        se_map = {se.id: se for se in sentences}
        return [
            {"sentence": se_map[int(rec[0])], "langid": int(rec[1])} for rec in recs
        ]

    # Guard against infinite loop.
    last_batch_ids = []

    output_function(f"Updating data for {count} sentences.")
    batch_size = 1000
    pr = ProgressReporter(count, output_function, report_every=batch_size)
    batch = _get_next_batch(batch_size)
    while len(batch) > 0:
        curr_batch_ids = [se_langid["sentence"].id for se_langid in batch]
        if last_batch_ids == curr_batch_ids:
            raise RuntimeError("Sentences not getting updated correctly.")

        for se_langid in batch:
            pr.increment()
            sentence = se_langid["sentence"]
            lang = supported_langs[se_langid["langid"]]
            if lang is None:
                raise RuntimeError(f"Logic err: Missing langid={se_langid['langid']}")
            sentence.set_lowercase_text(lang.parser)
            session.add(sentence)
        session.commit()

        last_batch_ids = curr_batch_ids
        batch = _get_next_batch(batch_size)

    session.commit()
    output_function("Done.")


def _update_term_images(session, output_function):
    """
    Fix TermImage sources (ref https://github.com/LuteOrg/lute-v3/issues/582)

    Prior to issue 582, images were stored in the db as url-like items,
    "/userimages/{language_id}/{term}.jpg".

    e.g. wordimages.wisource = "/userimages/2/thiết_kế_nội_thất.jpeg", including
    zero-width spaces.  This routine removes the "/userimages/{language_id}/"
    from the start of the strings.

    Also, some images didn't have ".jpg" at the end ... this adds that.
    """

    def _fix_source(s):
        "Remove the leading userimages and languageid, add .jpeg if needed."
        parts = s.split("/", 3)
        ret = parts[-1]
        if not ret.endswith(".jpeg"):
            ret = f"{ret}.jpeg"
        return ret

    stmt = select(TermImage).where(TermImage.source.contains("userimages"))
    recalc = session.execute(stmt).scalars().all()
    if len(recalc) == 0:
        # Nothing to calculate, quit.
        return

    batch_size = 1000
    output_function(f"Fixing image sources for {len(recalc)} word images.")
    pr = ProgressReporter(len(recalc), output_function, report_every=batch_size)
    n = 0
    for ti in recalc:
        pr.increment()
        ti.source = _fix_source(ti.source)
        session.add(ti)
        n += 1
        if n % batch_size == 0:
            session.commit()

    # Any remaining.
    session.commit()
    output_function("Done.")


def clean_data(session, output_function):
    "Clean all data as required, sending messages to output_function."
    _set_pdf_texts_word_count(session, output_function)
    _set_texts_word_count(session, output_function)
    _load_sentence_textlc(session, output_function)
    _update_term_images(session, output_function)
