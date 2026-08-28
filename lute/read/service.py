"""
Reading helpers.
"""

import math
import os
import re
from collections import defaultdict
from datetime import datetime
import functools
from flask import current_app
from pypdf import PdfReader
from lute.models.term import Term, Status
from lute.models.book import Text, WordsRead
from lute.models.repositories import BookRepository, UserSettingRepository
from lute.book.stats import Service as StatsService
from lute.read.render.service import Service as RenderService
from lute.read.render.calculate_textitems import get_string_indexes
from lute.term.model import Repository

# from lute.utils.debug_helpers import DebugTimer

# Extracted PDF page word boxes, keyed by (abs path, page number).
# Extraction is not cheap and users flip between pages often.  The
# cache holds only positions, never terms or session objects.
_pdf_page_words_cache = {}


def _pdf_char_width(ch, fs):
    "Estimated glyph width in PDF units, for hotspot boxes."
    o = ord(ch)
    if (
        0x2E80 <= o <= 0x9FFF  # CJK radicals through unified ideographs
        or 0xAC00 <= o <= 0xD7AF  # Hangul syllables
        or 0xF900 <= o <= 0xFAFF  # CJK compatibility ideographs
        or 0xFF00 <= o <= 0xFF60  # Fullwidth forms
    ):
        return fs
    if ch == " ":
        return fs * 0.3
    if ch.isupper():
        return fs * 0.67
    return fs * 0.5


def _extract_pdf_page_words(pdf_abs_path, pagenum):
    """
    Extract the reading-order words of one PDF page (1-based).

    Uses pypdf's visitor_text to capture text runs with their baseline
    positions, groups the runs into lines, and splits lines into words.
    Boxes are estimates; the reading screen's JavaScript refines them
    against pdf.js's exact glyph geometry after rendering.

    Returns (page_width, page_height, words), coordinates in PDF points
    with top-left origin, each word
    {"text", "x", "y", "width", "height", "line"}.
    """
    key = (os.path.abspath(pdf_abs_path), pagenum)
    if key in _pdf_page_words_cache:
        return _pdf_page_words_cache[key]

    reader = PdfReader(pdf_abs_path)
    page = reader.pages[pagenum - 1]
    pw = float(page.mediabox.width)
    ph = float(page.mediabox.height)

    runs = []

    def _visit(text, cm, tm, font_dict, font_size):
        if not text or not text.strip():
            return
        # Trailing newlines are pypdf artifacts of move-text-position
        # operators, not glyphs.
        clean = text.replace("\r", "").replace("\n", "")
        if not clean.strip():
            return
        tf = float(font_size) if font_size and float(font_size) > 0 else 10.0
        # The Tf size is not always the rendered size: generators such as
        # the one behind the Erin manga PDF write "/F1 1 Tf" and do the
        # real scaling in the text matrix (Tm).  Take the vertical scale
        # of the combined CTM x Tm matrices as the font size so line
        # boxes match the rendered glyphs.
        tm_sy = math.sqrt((tm[2] ** 2) + (tm[3] ** 2)) if tm else 0.0
        if tm_sy < 1e-6 and tm:
            tm_sy = math.sqrt((tm[0] ** 2) + (tm[1] ** 2))
        cm_sy = math.sqrt((cm[2] ** 2) + (cm[3] ** 2)) if cm else 0.0
        if cm_sy < 1e-6 and cm:
            cm_sy = math.sqrt((cm[0] ** 2) + (cm[1] ** 2))
        fs = tf * (tm_sy or 1.0) * (cm_sy or 1.0)
        if fs <= 0:
            fs = 10.0
        # Device position = CTM (cm) applied to the text matrix (tm).
        x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        runs.append({"text": clean, "x": x, "y": y, "fs": fs})

    page.extract_text(visitor_text=_visit)

    # Group runs into lines by baseline y, top to bottom, then order
    # each line's runs left to right.
    runs.sort(key=lambda r: (-r["y"], r["x"]))
    lines = []
    for run in runs:
        placed = False
        for line in lines:
            if abs(run["y"] - line["y"]) <= max(run["fs"] * 0.5, 2.0):
                line["runs"].append(run)
                line["fs"] = max(line["fs"], run["fs"])
                placed = True
                break
        if not placed:
            lines.append({"y": run["y"], "fs": run["fs"], "runs": [run]})
    lines.sort(key=lambda l: -l["y"])

    words = []
    for li, line in enumerate(lines):
        line["runs"].sort(key=lambda r: r["x"])
        fs = line["fs"]
        top = ph - line["y"] - fs * 0.86
        height = fs * 1.08
        # Split each run's own text into words: every run starts at its
        # own baseline x, so offsets must not span concatenated runs.
        for run in line["runs"]:
            for match in re.finditer(r"\S+", run["text"]):
                est_width = sum(
                    _pdf_char_width(c, fs)
                    for c in run["text"][match.start() : match.end()]
                )
                x_before = sum(
                    _pdf_char_width(c, fs) for c in run["text"][: match.start()]
                )
                words.append(
                    {
                        "text": match.group(0),
                        "x": run["x"] + x_before,
                        "y": top,
                        "width": est_width,
                        "height": height,
                        "line": li,
                    }
                )

    result = (pw, ph, words)
    if len(_pdf_page_words_cache) > 100:
        _pdf_page_words_cache.clear()
    _pdf_page_words_cache[key] = result
    return result


class TermPopup:
    "Popup data for a term."

    # pylint: disable=too-many-instance-attributes
    def __init__(self, term):
        self.term = term
        self.term_text = self._clean(term.text)
        self.parents_text = ", ".join([self._clean(p.text) for p in term.parents])
        self.translation = self._clean(term.translation)
        self.romanization = self._clean(term.romanization)
        self.tags = [tt.text for tt in term.term_tags]
        self.flash = self._clean(term.get_flash_message())
        self.image = term.get_current_image()
        self.popup_image_data = self._get_popup_image_data()

        # Final data to include in popup.
        self.parents = []
        self.components = []

    def _clean(self, t):
        "Clean text for popup usage."
        zws = "\u200B"
        ret = (t or "").strip()
        ret = ret.replace(zws, "")
        ret = ret.replace("\n", "<br />")
        return ret

    @property
    def show(self):
        "Calc if should show.  Must be deferred as values can be changed."
        checks = [self.romanization != "", self.translation != "", len(self.tags) > 0]
        return len([b for b in checks if b]) > 0

    def term_and_parents_text(self):
        "Return term text with parents if any."
        ret = self.term_text
        if self.parents_text != "":
            ret = f"{ret} ({self.parents_text})"
        return ret

    def _get_popup_image_data(self):
        "Get images"
        # Don't include component images in the hover for now,
        # it can get confusing!
        # ref https://github.com/LuteOrg/lute-v3/issues/355
        terms = [self.term, *self.term.parents]

        def _make_image_url(t):
            return f"/userimages/{t.language.id}/{t.get_current_image()}"

        images = [(_make_image_url(t), t.text) for t in terms if t.get_current_image()]
        imageresult = defaultdict(list)
        for key, value in images:
            imageresult[key].append(self._clean(value))
        # Convert lists to comma-separated strings
        return {k: ", ".join(v) for k, v in imageresult.items()}


class Service:
    "Service."

    def __init__(self, session):
        self.session = session

    def update_start_date(self, book, pagenum):
        "Lightweight update of text.start_date."
        text = book.text_at_page(pagenum)
        text.start_date = datetime.utcnow()
        book.current_tx_id = text.id
        self.session.add(text)
        self.session.add(book)
        self.session.commit()

    def mark_page_read(
        self, bookid, pagenum, mark_rest_as_known, mark_rest_of_book_known=False
    ):
        "Mark page as read, record stats, rest as known."
        br = BookRepository(self.session)
        book = br.find(bookid)
        text = book.text_at_page(pagenum)
        d = datetime.utcnow()
        text.read_date = d

        # Manga books have empty page text, so word_count is None; use
        # 0 so the WordsRead row satisfies its NOT NULL constraint.
        w = WordsRead(text, d, text.word_count or 0)
        self.session.add(text)
        self.session.add(w)
        self.session.commit()
        if mark_rest_of_book_known:
            self.set_book_unknowns_to_known(book)
        elif mark_rest_as_known:
            self.set_unknowns_to_known(text)

    def set_unknowns_to_known(self, text: Text):
        """
        Given a text, create new Terms with status Well-Known
        for any new Terms.
        """
        rs = RenderService(self.session)
        paragraphs = rs.get_paragraphs(text.text, text.book.language)
        self._save_new_status_0_terms(paragraphs)

        unknowns = [
            ti.term
            for para in paragraphs
            for sentence in para
            for ti in sentence
            if ti.is_word and ti.term.status == 0
        ]

        batch_size = 100
        i = 0

        for t in unknowns:
            t.status = Status.WELLKNOWN
            self.session.add(t)
            i += 1
            if i % batch_size == 0:
                self.session.commit()

        # Commit any remaining.
        self.session.commit()

        # Mark the book's stats stale only when statuses actually changed,
        # so the home page recomputes the status distribution.  (Marking
        # stale on every page open made returning home re-calculate the
        # whole sample synchronously, which was slow for long books.)
        if unknowns:
            StatsService(self.session).mark_stale(text.book)

    def set_book_unknowns_to_known(self, book):
        """
        Given a book, create new Terms with status Well-Known for any
        new Terms on every page of the book.
        """
        rs = RenderService(self.session)
        batch_size = 100
        i = 0

        for text in book.texts:
            paragraphs = rs.get_paragraphs(text.text, text.book.language)
            self._save_new_status_0_terms(paragraphs)

            unknowns = [
                ti.term
                for para in paragraphs
                for sentence in para
                for ti in sentence
                if ti.is_word and ti.term.status == 0
            ]

            for t in unknowns:
                t.status = Status.WELLKNOWN
                self.session.add(t)
                i += 1
                if i % batch_size == 0:
                    self.session.commit()

        # Commit any remaining.
        self.session.commit()

        # Mark the book's stats stale only when statuses actually changed,
        # so the home page recomputes the status distribution.
        if i > 0:
            StatsService(self.session).mark_stale(book)

    def set_terms_to_known(self, wordids, book=None):
        """
        Set the given term ids to Well-Known, skipping any that are
        not currently unknown.  Used when a sub-screen is finished.
        """
        seen = set()
        ids = []
        for w in wordids or []:
            try:
                i = int(w)
            except (TypeError, ValueError):
                continue
            if i > 0 and i not in seen:
                seen.add(i)
                ids.append(i)
        if not ids:
            return 0

        batch_size = 100
        i = 0

        terms = (
            self.session.query(Term)
            .filter(Term.id.in_(ids), Term.status == Status.UNKNOWN)
            .all()
        )
        for t in terms:
            t.status = Status.WELLKNOWN
            self.session.add(t)
            i += 1
            if i % batch_size == 0:
                self.session.commit()

        # Commit any remaining.
        self.session.commit()

        if i > 0 and book is not None:
            StatsService(self.session).mark_stale(book)

        return i

    def bulk_status_update(self, text: Text, terms_text_array, new_status):
        """
        Given a text and list of terms, update or create new terms
        and set the status.
        """
        language = text.book.language
        repo = Repository(self.session)
        for term_text in terms_text_array:
            t = repo.find_or_new(language.id, term_text)
            t.status = new_status
            repo.add(t)
        repo.commit()

    def _save_new_status_0_terms(self, paragraphs):
        "Add status 0 terms for new textitems in paragraph."
        tis_with_new_terms = [
            ti
            for para in paragraphs
            for sentence in para
            for ti in sentence
            if ti.is_word and ti.term.id is None and ti.term.status == 0
        ]

        for ti in tis_with_new_terms:
            self.session.add(ti.term)
        self.session.commit()

    def _get_reading_data(self, dbbook, pagenum, track_page_open=False):
        "Get paragraphs, set text.start_date if needed."
        text = dbbook.text_at_page(pagenum)

        # Only load sentences if they don't already exist.
        # load_sentences() re-parses the full text with the language
        # parser (e.g. MeCab), which is expensive and redundant on
        # every page load.  The reading page rendering uses TextItems
        # from get_paragraphs(), not Sentence objects — sentences are
        # only needed by Anki export and term detail views.
        if not text.sentences:
            text.load_sentences()

        # Opening a page doesn't change any term statuses, so we do NOT
        # mark the book's stats stale here — that would force the home
        # screen to re-calculate synchronously on return (slow for long
        # books).  Stats are only marked stale when statuses actually
        # change, e.g. via set_unknowns_to_known / bulk_update_status.

        if track_page_open:
            text.start_date = datetime.utcnow()
            dbbook.current_tx_id = text.id

        self.session.add(dbbook)
        self.session.add(text)
        self.session.commit()

        lang = text.book.language
        rs = RenderService(self.session)
        paragraphs = rs.get_paragraphs(text.text, lang)

        self._save_new_status_0_terms(paragraphs)

        return paragraphs

    def get_paragraphs(self, dbbook, pagenum):
        "Get the paragraphs for the book."
        return self._get_reading_data(dbbook, pagenum, False)

    def start_reading(self, dbbook, pagenum):
        "Start reading a page in the book, getting paragraphs."
        return self._get_reading_data(dbbook, pagenum, True)

    def manga_page_context(self, dbbook, pagenum, track_page_open=False):
        """
        Build the render context for one Mokuro manga page: the image URL
        and the text blocks with tokenized text items.

        Returns None if the book has no manga data, or the page is out
        of range.
        """
        manga = getattr(dbbook, "manga", None) or {}
        pages = manga.get("pages") or []
        if not 1 <= pagenum <= len(pages):
            return None
        page = pages[pagenum - 1]

        # Track page open / current position, same as text books.
        text = dbbook.text_at_page(pagenum)
        if track_page_open:
            text.start_date = datetime.utcnow()
            dbbook.current_tx_id = text.id
        self.session.add(dbbook)
        self.session.add(text)
        self.session.commit()

        manga_path = (dbbook.manga_path or "").strip("/")
        raw_img_path = (page.get("img_path") or "").lstrip("/").replace("\\", "/")

        # Runtime fallback: resolve the image path against the actual
        # extracted files on disk.  Mokuro-generated zips commonly
        # place images under a volume/ subdirectory while the .mokuro
        # JSON records just the basename in img_path.  New imports fix
        # this up during extract_manga(), but already-imported books
        # need a runtime lookup.
        resolved_img_path = raw_img_path
        if manga_path and raw_img_path:
            manga_abs = os.path.join(current_app.static_folder, manga_path)
            if os.path.isdir(manga_abs):
                raw_abs = os.path.normpath(os.path.join(manga_abs, raw_img_path))
                if not os.path.isfile(raw_abs):
                    # Try volume subdir, then basename scan.
                    volume = ((manga.get("volume") or "").strip()
                              .replace("\\", "/"))
                    candidates = []
                    if volume:
                        candidates.append(
                            os.path.normpath(
                                os.path.join(manga_abs, volume, raw_img_path)
                            )
                        )
                    # Fallback: walk the manga directory and match by
                    # basename, preferring paths that mention volume.
                    base = os.path.basename(raw_img_path).lower()
                    matches = []
                    for root, _dirs, files in os.walk(manga_abs):
                        for f in files:
                            if os.path.basename(f).lower() == base:
                                matches.append(os.path.join(root, f))
                    if matches:
                        def _match_key(p):
                            has_vol = (
                                bool(volume)
                                and volume.lower() in p.lower()
                            )
                            return (0 if has_vol else 1, len(p))
                        matches.sort(key=_match_key)
                        candidates.insert(0, matches[0])
                    for c in candidates:
                        if os.path.isfile(c) and os.path.realpath(c).startswith(
                            os.path.realpath(manga_abs) + os.sep
                        ):
                            try:
                                rel = os.path.relpath(c, manga_abs)
                                resolved_img_path = rel.replace("\\", "/")
                                break
                            except ValueError:
                                continue

        img_url = f"/static/{manga_path}/{resolved_img_path.lstrip('/')}"

        rs = RenderService(self.session)
        lang = dbbook.language
        blocks = []
        order = 0
        for bi, block in enumerate(page.get("blocks") or []):
            box = block.get("box") or [0, 0, 0, 0]
            line_items = []
            for li, line in enumerate(block.get("lines") or []):
                # A mokuro "line" can hold several physical text rows
                # joined by newlines or the "¶" paragraph marker; split
                # them so each row renders on its own line in the box
                # instead of a single unbroken row.
                for phys in re.split(r"[¶\r\n]+", line):
                    if not phys.strip():
                        continue
                    items = rs.get_textitems(phys, lang)
                    kept = []
                    for it in items:
                        # Guard against paragraph markers leaking through
                        # from the parser.
                        if it.text == "¶":
                            continue
                        it.paragraph_number = bi + 1
                        it.sentence_number = li + 1
                        it.index = order
                        order += 1
                        kept.append(it)
                    line_items.append(kept)
            blocks.append(
                {
                    "box": box,
                    "vertical": bool(block.get("vertical")),
                    "font_size": block.get("font_size") or 0,
                    "line_items": line_items,
                }
            )

        # Save new status-0 terms so the words have data-wid on the
        # next page load (mirrors _save_new_status_0_terms).
        self._save_new_manga_terms(blocks)

        return {
            "page_num": pagenum,
            "img_url": img_url,
            "img_width": page.get("img_width") or 100,
            "img_height": page.get("img_height") or 100,
            "blocks": blocks,
        }

    def _save_new_manga_terms(self, blocks):
        """
        Add status-0 terms found in manga text blocks.

        Each line is tokenized with its own get_textitems() call, so a
        word repeated within a page can produce several distinct,
        unsaved Term objects for the same text; de-duplicate them by
        (language, text_lc) before committing to avoid UNIQUE
        constraint violations on words.WoLgID + words.WoTextLC.
        """
        seen = set()
        new_terms = []
        for block in blocks:
            for line_items in block["line_items"]:
                for ti in line_items:
                    if (
                        ti.is_word
                        and ti.term is not None
                        and ti.term.id is None
                        and ti.term.status == 0
                    ):
                        key = (ti.term.language.id, ti.term.text_lc)
                        if key not in seen:
                            seen.add(key)
                            new_terms.append(ti.term)
        for t in new_terms:
            self.session.add(t)
        self.session.commit()

    def pdf_page_context(self, dbbook, pagenum, track_page_open=False):
        """
        Build the render context for one PDF book page: the pdf URL,
        page dimensions, and the ordered words with tokenized text items.

        Returns None if the book has no pdf file, or the page is out of
        range.
        """
        pdf_path = (dbbook.pdf_path or "").strip("/")
        if not pdf_path or not 1 <= pagenum <= dbbook.page_count:
            return None

        # Track page open / current position, same as text books.
        text = dbbook.text_at_page(pagenum)
        if track_page_open:
            text.start_date = datetime.utcnow()
            dbbook.current_tx_id = text.id
        self.session.add(dbbook)
        self.session.add(text)
        self.session.commit()

        pdf_abs = os.path.join(current_app.static_folder, pdf_path)
        if not os.path.isfile(pdf_abs):
            return None

        page_width, page_height, words = _extract_pdf_page_words(pdf_abs, pagenum)

        rs = RenderService(self.session)
        lang = dbbook.language
        word_contexts = []
        order = 0
        for word in words:
            items = rs.get_textitems(word["text"], lang)
            kept = []
            for it in items:
                # Guard against paragraph markers leaking through
                # from the parser.
                if it.text == "¶":
                    continue
                it.paragraph_number = word["line"] + 1
                it.sentence_number = word["line"] + 1
                it.index = order
                order += 1
                kept.append(it)
            if not kept:
                continue
            # Estimated box in % of the page.  JavaScript refines this
            # against pdf.js geometry after rendering; without it the
            # words are still usable in roughly the right places.
            box = [
                word["x"] / page_width * 100,
                word["y"] / page_height * 100,
                max(word["width"], 1) / page_width * 100,
                max(word["height"], 1) / page_height * 100,
            ]
            word_contexts.append(
                {
                    "box": box,
                    "items": kept,
                    "char_lens": [len(it.text) for it in kept],
                }
            )

        # Save new status-0 terms so the words have data-wid on the
        # next page load (mirrors _save_new_manga_terms).
        self._save_new_pdf_terms(word_contexts)

        return {
            "page_num": pagenum,
            "pdf_url": f"/static/{pdf_path}",
            "page_width": page_width,
            "page_height": page_height,
            "words": word_contexts,
        }

    def _save_new_pdf_terms(self, word_contexts):
        """
        Add status-0 terms found in PDF page words.

        Each word is tokenized with its own get_textitems() call, so a
        word repeated within a page can produce several distinct,
        unsaved Term objects for the same text; de-duplicate them by
        (language, text_lc) before committing to avoid UNIQUE
        constraint violations on words.WoLgID + words.WoTextLC.
        """
        seen = set()
        new_terms = []
        for word in word_contexts:
            for ti in word["items"]:
                if (
                    ti.is_word
                    and ti.term is not None
                    and ti.term.id is None
                    and ti.term.status == 0
                ):
                    key = (ti.term.language.id, ti.term.text_lc)
                    if key not in seen:
                        seen.add(key)
                        new_terms.append(ti.term)
        for t in new_terms:
            self.session.add(t)
        self.session.commit()

    def _sort_components(self, term, components):
        "Sort components by min position in string and length."
        component_and_pos = []
        for c in components:
            c_indices = [
                loc[1] for loc in get_string_indexes([c.text_lc], term.text_lc)
            ]

            # Sometimes the components aren't found
            # in the string, which makes no sense ...
            # ref https://github.com/LuteOrg/lute-v3/issues/474
            if len(c_indices) > 0:
                component_and_pos.append([c, min(c_indices)])

        def compare(a, b):
            # Lowest position (closest to front of string) sorts first.
            if a[1] != b[1]:
                return -1 if (a[1] < b[1]) else 1
            # Longest sorts first.
            alen = len(a[0].text)
            blen = len(b[0].text)
            return -1 if (alen > blen) else 1

        component_and_pos.sort(key=functools.cmp_to_key(compare))
        return [c[0] for c in component_and_pos]

    def get_popup_data(self, termid):
        "Get popup data, or None if popup shouldn't be shown."
        term = self.session.get(Term, termid)
        if term is None:
            return None

        repo = UserSettingRepository(self.session)
        show_components = int(repo.get_value("term_popup_show_components")) == 1
        components = []
        if show_components:
            rs = RenderService(self.session)
            components = [
                c
                for c in rs.find_all_Terms_in_string(term.text, term.language)
                if c.id != term.id and c.status != Status.UNKNOWN
            ]

        t = TermPopup(term)
        if (
            t.show is False
            and t.image is None
            and len(term.parents) == 0
            and len(components) == 0
        ):
            # Nothing to show."
            return None

        parent_data = [TermPopup(p) for p in term.parents]

        promote_parent_trans = int(
            repo.get_value("term_popup_promote_parent_translation")
        )
        if (promote_parent_trans == 1) and len(term.parents) == 1:
            ptrans = parent_data[0].translation
            if t.translation == "":
                t.translation = ptrans
            if t.translation == ptrans:
                parent_data[0].translation = ""

        component_data = [TermPopup(c) for c in self._sort_components(term, components)]

        t.parents = [p for p in parent_data if p.show]
        t.components = [c for c in component_data if c.show]
        return t
