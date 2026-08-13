"""
Book statistics.
"""

import json
import re
from sqlalchemy import select, text
from lute.read.render.service import Service as RenderService
from lute.models.book import Book, BookStats
from lute.models.repositories import UserSettingRepository

# from lute.utils.debug_helpers import DebugTimer

# Manga texts can contain thousands of lines; the status distribution is
# derived from a sample, matching how regular books sample a few pages
# (see _get_sample_texts).
MANGA_STATS_SAMPLE_LINES = 1000


class Service:
    "Service."

    def __init__(self, session):
        self.session = session

    def _last_n_pages(self, book, txindex, n):
        "Get next n pages, or at least n pages."
        start_index = max(0, txindex - n)
        end_index = txindex + n
        texts = book.texts[start_index:end_index]
        return texts[-n:]

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
        txindex = 0
        if (book.current_tx_id or 0) != 0:
            for t in book.texts:
                if t.id == book.current_tx_id:
                    break
                txindex += 1

        repo = UserSettingRepository(self.session)
        sample_size = int(repo.get_value("stats_calc_sample_size") or 5)
        texts = self._last_n_pages(book, txindex, sample_size)
        return texts

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
            # sample the text and render it in a single call.
            lines = self._get_manga_text_lines(book)
            lines = lines[:MANGA_STATS_SAMPLE_LINES]
            textitems = service.get_textitems("\n".join(lines), book.language, mw)
        else:
            texts = self._get_sample_texts(book)
            textitems = []
            for tx in texts:
                textitems.extend(service.get_textitems(tx.text, book.language, mw))
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
        book_ids_with_stats = select(BookStats.BkID).scalar_subquery()
        books_to_update = (
            self.session.query(Book).filter(~Book.id.in_(book_ids_with_stats)).all()
        )
        books = [b for b in books_to_update if b.is_supported or b.book_type == "manga"]
        for book in books:
            stats = self._calculate_stats(book)
            self._update_stats(book, stats)

    def mark_stale(self, book):
        "Mark a book's stats as stale to force refresh."
        bk_id = book.id
        self.session.query(BookStats).filter_by(BkID=bk_id).delete()
        self.session.commit()

    def get_stats(self, book):
        "Gets stats from the cache if available, or calculates."
        bk_id = book.id
        stats = self.session.query(BookStats).filter_by(BkID=bk_id).first()
        if stats is None or stats.status_distribution is None:
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
        self.session.merge(s)
        self.session.commit()
