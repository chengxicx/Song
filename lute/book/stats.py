"""
Book statistics.
"""

import json
import os
import re
from flask import current_app
from sqlalchemy import select, text
from lute.read.render.service import Service as RenderService
from lute.models.book import Book, BookStats

# from lute.utils.debug_helpers import DebugTimer

# Manga texts can contain thousands of lines; the status distribution is
# derived from a sample, matching how regular books sample a few pages
# (see _get_uniform_sample_texts).
MANGA_STATS_SAMPLE_LINES = 1000

# For regular (non-manga) books the difficulty estimate is based on a
# sample spread evenly across the whole book (see _get_uniform_sample_texts).
SAMPLE_PAGE_COUNT = 15

# Difficulty thresholds for the "New word" percentage.  A book is banded as
#   * EASY: new_word_percent < NEW_WORD_EASY_PERCENT
#   * CHAL: NEW_WORD_EASY_PERCENT <= new_word_percent <= NEW_WORD_CHAL_PERCENT
#   * HARD: new_word_percent > NEW_WORD_CHAL_PERCENT
# These single source of truth is shared by the book list filtering
# (lute/book/datatables.py) and the frontend display, so they never drift.
NEW_WORD_EASY_PERCENT = 10
NEW_WORD_CHAL_PERCENT = 20


def get_difficulty_label(new_word_percent):
    """
    Map a book's new-word percentage to its difficulty band.

    Returns a (label, color_class, description) tuple:
      - label: one of 'EASY' / 'CHAL' / 'HARD'
      - color_class: the CSS class used to colour the badge
        ('new-word-easy' / 'new-word-chal' / 'new-word-hard')
      - description: a human-readable explanation for tooltips.

    A null percentage (book not yet parsed) is treated as EASY.
    """
    if new_word_percent is None or new_word_percent < NEW_WORD_EASY_PERCENT:
        return (
            "EASY",
            "new-word-easy",
            f"Easy: under {NEW_WORD_EASY_PERCENT}% of words are new.",
        )
    if new_word_percent <= NEW_WORD_CHAL_PERCENT:
        return (
            "CHAL",
            "new-word-chal",
            f"Challenging: {NEW_WORD_EASY_PERCENT}-{NEW_WORD_CHAL_PERCENT}% of words are new.",
        )
    return (
        "HARD",
        "new-word-hard",
        f"Hard: over {NEW_WORD_CHAL_PERCENT}% of words are new.",
    )


def difficulty_sql_case(column):
    """
    Build the SQL CASE fragments that map `column` (a SQL new-word-percent
    expression) to its difficulty label, CSS colour class and description.

    Used by datatables.py so the book list computes the difficulty columns
    without duplicating the thresholds.
    """
    easy = NEW_WORD_EASY_PERCENT
    chal = NEW_WORD_CHAL_PERCENT
    return {
        "label": f"""
        CASE
            WHEN {column} IS NULL OR {column} < {easy} THEN 'EASY'
            WHEN {column} <= {chal} THEN 'CHAL'
            ELSE 'HARD'
        END""",
        "color": f"""
        CASE
            WHEN {column} IS NULL OR {column} < {easy} THEN 'new-word-easy'
            WHEN {column} <= {chal} THEN 'new-word-chal'
            ELSE 'new-word-hard'
        END""",
        "description": f"""
        CASE
            WHEN {column} IS NULL OR {column} < {easy}
                THEN 'Easy: under {easy}% of words are new.'
            WHEN {column} <= {chal}
                THEN 'Challenging: {easy}-{chal}% of words are new.'
            ELSE 'Hard: over {chal}% of words are new.'
        END""",
    }


def difficulty_filter_sql(column, level):
    """
    Return the SQL WHERE fragment that selects books in the given
    difficulty band, or None if `level` is not a known band.

    `column` is a SQL new-word-percent expression.
    """
    easy = NEW_WORD_EASY_PERCENT
    chal = NEW_WORD_CHAL_PERCENT
    level = level.upper()
    if level == "EASY":
        return f"({column} IS NULL OR {column} < {easy})"
    if level == "CHAL":
        return f"({column} >= {easy} AND {column} <= {chal})"
    if level == "HARD":
        return f"({column} > {chal})"
    return None


class Service:
    "Service."

    def __init__(self, session):
        self.session = session

    def _get_uniform_sample_texts(self, book):
        """
        Get a representative sample of pages spread evenly across the whole
        book.

        The total sampled pages is capped at SAMPLE_PAGE_COUNT.  When the
        book has fewer pages than that, every page is sampled.  Otherwise
        ~SAMPLE_PAGE_COUNT evenly spaced positions are chosen from the whole
        book (start, middle and end all get coverage), so the difficulty
        estimate reflects the entire book rather than only the region around
        the current reading position.
        """
        page_count = len(book.texts)
        if page_count == 0:
            return []
        if page_count <= SAMPLE_PAGE_COUNT:
            return list(book.texts)
        # Evenly distribute sample positions across the whole book.
        step = (page_count - 1) / (SAMPLE_PAGE_COUNT - 1)
        indexes = sorted({round(step * i) for i in range(SAMPLE_PAGE_COUNT)})
        return [book.texts[i] for i in indexes]

    def _get_manga_text_lines(self, book):
        """
        Extract all text lines from a manga book's mokuro data.

        Returns a flat list of text strings, one per physical line
        (split on ¶, \\r, \\n), suitable for tokenization.
        """
        manga = book.manga or {}
        pages = manga.get("pages") or []
        lines = []
        for page in pages:
            for block in page.get("blocks") or []:
                for line in block.get("lines") or []:
                    for phys in re.split(r"[¶\r\n]+", line):
                        if phys.strip():
                            lines.append(phys.strip())
        return lines

    def _get_sample_texts(self, book):
        "Get texts to use as sample."
        return self._get_uniform_sample_texts(book)

    def _get_pdf_sample_lines(self, book):
        """
        Extract a sample of text lines from a pdf book's file, spread
        evenly across the book.

        Pdf pages keep an *empty* page text by design -- their words are
        read out of the original PDF when the page is opened (see
        BookService.set_pdf_page_word_counts) -- so the plain-text
        sampler finds nothing.  Extract the words of a small number of
        pages straight from the file instead, mirroring how the manga
        handling below reads its text out of the mokuro data.
        """
        pdf_rel = (book.pdf_path or "").strip("/")
        if not pdf_rel:
            return []
        try:
            pdf_abs = os.path.join(current_app.static_folder, pdf_rel)
        except Exception:  # pylint: disable=broad-exception-caught
            # Outside a request (e.g. an admin shell) there is no static
            # folder to resolve against, so there is nothing to sample.
            return []
        if not os.path.isfile(pdf_abs):
            return []

        page_count = book.page_count
        if page_count == 0:
            return []
        # Evenly distribute sample positions across the whole book.
        if page_count <= SAMPLE_PAGE_COUNT:
            pagenums = list(range(1, page_count + 1))
        else:
            step = (page_count - 1) / (SAMPLE_PAGE_COUNT - 1)
            pagenums = sorted({round(step * i) + 1 for i in range(SAMPLE_PAGE_COUNT)})

        # Imported lazily: lute.read.service imports lute.book.stats, so
        # a module-level import here would be circular.
        from lute.read.service import (  # pylint: disable=import-outside-toplevel
            _extract_pdf_page_words,
        )

        lines = []
        for pagenum in pagenums:
            try:
                _width, _height, words = _extract_pdf_page_words(pdf_abs, pagenum)
            except Exception:  # pylint: disable=broad-exception-caught
                # A page the extractor chokes on contributes nothing;
                # the remaining sampled pages still give a usable
                # distribution.
                continue
            for word in words:
                word_text = (word.get("text") or "").strip()
                if word_text:
                    lines.append(word_text)
        return lines

    def calc_status_distribution(self, book):
        """
        Calculate statuses and count of unique words per status.

        Does a full render of a small number of pages
        to calculate the distribution.
        """

        # DebugTimer.clear_total_summary()
        # dt = DebugTimer("get_status_distribution", display=False)
        service = RenderService(self.session)
        mw = service.get_multiword_indexer(book.language)

        if book.book_type == "manga":
            # For manga books, extract text directly from the mokuro JSON.
            # Rendering every line separately is extremely slow (each
            # get_textitems call re-parses and re-queries terms), so
            # sample the text and render it in batches below.
            lines = self._get_manga_text_lines(book)
            lines = lines[:MANGA_STATS_SAMPLE_LINES]
        elif book.book_type == "pdf":
            # Pdf pages keep an empty page text (their words come from
            # the pdf file), so sample pages straight out of the file,
            # like the manga handling above.
            lines = self._get_pdf_sample_lines(book)
        else:
            texts = self._get_sample_texts(book)
            lines = [t.text for t in texts if t.text]

        # Render the sampled lines in batches: calling get_textitems()
        # once per page is slow (each call re-parses and re-queries
        # terms), but a single giant call would be unbounded.  Joining
        # lines and processing ~500 lines per batch keeps throughput
        # good with bounded memory.
        textitems = []
        for i in range(0, len(lines), 500):
            chunk = "\n".join(lines[i : i + 500])
            textitems.extend(service.get_textitems(chunk, book.language, mw))
        # # Old slower code:
        # text_sample = "\n".join([t.text for t in texts])
        # paras = get_paragraphs(text_sample, book.language) ... etc.
        # dt.step("get_paragraphs")

        textitems = [ti for ti in textitems if ti.is_word]
        statterms = {0: [], 1: [], 2: [], 3: [], 4: [], 5: [], 98: [], 99: []}
        for ti in textitems:
            statterms[ti.wo_status or 0].append(ti.text_lc)

        stats = {}
        for statusval, allterms in statterms.items():
            uniques = list(set(allterms))
            statterms[statusval] = uniques
            stats[statusval] = len(uniques)

        # dt.step("compiled")
        # DebugTimer.total_summary()

        return stats

    def calc_manga_word_count(self, book):
        """
        Count total words (tokens) in a manga book's mokuro text data.

        Returns None if the book is not a manga type or has no manga data.
        """
        if book.book_type != "manga":
            return None
        lines = self._get_manga_text_lines(book)
        # Parse the lines in batches: MeCab is much faster when it
        # receives a large chunk at once than when called once per line
        # (measured ~100x slower line by line), and pure tokenization
        # skips the expensive term lookups done by get_textitems().
        total = 0
        for i in range(0, len(lines), 500):
            chunk = "\n".join(lines[i : i + 500])
            tokens = book.language.get_parsed_tokens(chunk)
            total += sum(1 for tk in tokens if tk.is_word)
        return total

    def refresh_stats(self):
        "Refresh stats for all books requiring update."
        sql = "delete from bookstats where status_distribution is null"
        self.session.execute(text(sql))
        self.session.commit()
        fresh_ids = (
            select(BookStats.BkID).where(BookStats.stale.is_(False)).scalar_subquery()
        )
        stale_ids = (
            select(BookStats.BkID).where(BookStats.stale.is_(True)).scalar_subquery()
        )
        books_to_update = (
            self.session.query(Book)
            .filter((~Book.id.in_(fresh_ids)) | (Book.id.in_(stale_ids)))
            .all()
        )
        books = [b for b in books_to_update if b.is_supported or b.book_type == "manga"]
        for book in books:
            stats = self._calculate_stats(book)
            self._update_stats(book, stats)

    def mark_stale(self, book):
        "Mark a book's stats as stale; keep last-known values for display."
        bk_id = book.id
        s = self.session.query(BookStats).filter_by(BkID=bk_id).first()
        if s is None:
            s = BookStats(BkID=bk_id)
            self.session.add(s)
        s.stale = True
        self.session.commit()

    def get_stats(self, book):
        "Gets stats from the cache if available, or calculates."
        bk_id = book.id
        stats = self.session.query(BookStats).filter_by(BkID=bk_id).first()
        if (
            stats is None
            or stats.status_distribution is None
            or stats.stale
        ):
            newstats = self._calculate_stats(book)
            self._update_stats(book, newstats)
            stats = self.session.query(BookStats).filter_by(BkID=bk_id).first()
        return stats

    def _calculate_stats(self, book):
        "Calc stats for the book using the status distribution."
        status_distribution = self.calc_status_distribution(book)
        unknowns = status_distribution[0]
        new_words = unknowns + status_distribution[1]
        allunique = sum(status_distribution.values())

        percent = 0
        if allunique > 0:  # In case not parsed.
            percent = round(100.0 * unknowns / allunique)

        new_word_percent = 0
        if allunique > 0:
            new_word_percent = round(100.0 * new_words / allunique)

        manga_wc = self.calc_manga_word_count(book)

        return {
            "allunique": allunique,
            "unknowns": unknowns,
            "new_words": new_words,
            "percent": percent,
            "new_word_percent": new_word_percent,
            "distribution": json.dumps(status_distribution),
            "manga_word_count": manga_wc,
        }

    def _update_stats(self, book, stats):
        "Update BookStats for the given book."
        s = self.session.query(BookStats).filter_by(BkID=book.id).first()
        if s is None:
            s = BookStats(BkID=book.id)
        s.distinctterms = stats["allunique"]
        s.distinctunknowns = stats["unknowns"]
        s.unknownpercent = stats["percent"]
        s.new_word_percent = stats["new_word_percent"]
        s.status_distribution = stats["distribution"]
        s.manga_word_count = stats["manga_word_count"]
        s.stale = False
        self.session.merge(s)
        self.session.commit()
