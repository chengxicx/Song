"""
/read endpoints.
"""

import json
from flask import Blueprint, flash, request, render_template, redirect, jsonify
from lute.read.service import Service
from lute.read.render.service import Service as RenderService
from lute.read.forms import TextForm
from lute.term.model import Repository
from lute.term.routes import handle_term_form
from lute.settings.current import current_settings
from lute.models.book import Text
from lute.models.repositories import BookRepository, LanguageRepository
from lute.book.service import youtube_video_id
from lute.tts.routes import get_lang_code
from lute.db import db


bp = Blueprint("read", __name__, url_prefix="/read")

# Module-level cache for subtitle word HTML, keyed by (book id, srt_data).
# The tokenization of all cues (parser parse + term lookup + Jinja
# render per word) is expensive (10-20s for long videos).  The result
# is deterministic for a given set of cues + terms, so we compute it
# once and reuse it on subsequent page loads.  Each gunicorn worker
# has its own cache; that's fine — the first request per worker pays
# the cost, the rest are instant.
_yt_subtitle_words_cache = {}


def invalidate_yt_subtitle_cache(book_id=None):
    """Clear the subtitle word-HTML cache.

    Called after term status updates so the subtitle re-renders with
    fresh data-status-class values.  If book_id is given, only that
    book's entries are cleared; otherwise the entire cache is wiped.
    """
    if book_id is not None:
        for k in [k for k in _yt_subtitle_words_cache if k[0] == book_id]:
            _yt_subtitle_words_cache.pop(k, None)
    else:
        _yt_subtitle_words_cache.clear()


def _fmt_seconds(secs):
    "Format seconds as m:ss or h:mm:ss."
    secs = max(0, int(round(secs or 0)))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _subtitle_words_html(book):
    """
    Render word-by-word HTML for each subtitle cue, so the scrolling
    subtitle line can reuse the exact reading-page tokenization and
    click behavior.

    The book text is the cues joined by newlines, so tokenizing the
    joined text and splitting on the end-of-paragraph sentinel (¶)
    yields one chunk per cue.  Returns a list of HTML strings aligned
    with book.cues.

    Results are cached per book, keyed by (book id, srt_data) so that
    subtitle changes produce a fresh render (see
    _yt_subtitle_words_cache).
    """
    if (book.book_type or "") != "youtube":
        return []
    cache_key = (book.id, book.srt_data)
    cached = _yt_subtitle_words_cache.get(cache_key)
    if cached is not None:
        return cached
    cues = list(book.cues)
    if not cues:
        return []
    lang = book.language
    render_service = RenderService(db.session)
    # Internal newlines in a single cue are replaced with a space so
    # each cue maps to exactly one paragraph (and therefore one chunk).
    join_text = "\n".join((c.get("text") or "").replace("\n", " ") for c in cues)
    textitems = render_service.get_textitems(join_text, lang)

    # Save new status-0 terms created during subtitle tokenization.
    # Without this, every page load re-creates (and re-parses readings
    # for) the same terms, and subtitle words lack data-wid attributes
    # (causing the NaN/edit_term bug).
    new_terms = [
        ti.term for ti in textitems
        if ti.is_word and ti.term is not None
        and ti.term.id is None and ti.term.status == 0
    ]
    if new_terms:
        for t in new_terms:
            db.session.add(t)
        db.session.commit()

    chunks = []
    curr = []
    for ti in textitems:
        if ti.text == "¶":
            if curr:
                chunks.append(curr)
                curr = []
        else:
            curr.append(ti)
    if curr:
        chunks.append(curr)

    rendered = []
    for i, chunk in enumerate(chunks):
        snum = i + 1
        parts = []
        for ti in chunk:
            ti.sentence_number = snum
            parts.append(render_template("read/textitem.html", item=ti))
        rendered.append("".join(parts))
    # Pad/truncate so the list aligns with the cues.
    while len(rendered) < len(cues):
        rendered.append("")
    result = rendered[: len(cues)]
    _yt_subtitle_words_cache[cache_key] = result
    return result


def _render_book_page(book, pagenum, track_page_open=True):
    """
    Render a particular book page.
    """
    if not book.texts:
        flash(
            f"Book {book.title} has no pages (possibly the parser failed "
            f"to split text at creation time)."
        )
        return redirect("/", 302)

    lang = book.language
    show_highlights = current_settings["show_highlights"]
    lang_repo = LanguageRepository(db.session)
    term_dicts = lang_repo.all_dictionaries()[lang.id]["term"]

    book_type = book.book_type or ""
    yt_video_id = None
    if book_type == "youtube":
        yt_video_id = youtube_video_id(book.source_uri)
    srt_cues = []
    if book_type == "youtube":
        srt_cues = list(book.cues)
        for c in srt_cues:
            c["start_str"] = _fmt_seconds(c.get("start", 0))
            c["end_str"] = _fmt_seconds(c.get("end", 0))
        # Subtitle word HTML is loaded lazily via AJAX
        # (/read/youtube_subtitle_words/<id>) to avoid blocking the
        # initial page render — tokenizing all cues can take 10-20s
        # for long videos.

    return render_template(
        "read/index.html",
        hide_top_menu=True,
        is_rtl=lang.right_to_left,
        html_title=book.title,
        book=book,
        sentence_dict_uris=lang.sentence_dict_uris,
        page_num=pagenum,
        page_count=book.page_count,
        show_highlights=show_highlights,
        lang_id=lang.id,
        lang_code=get_lang_code(lang.name),
        track_page_open=track_page_open,
        term_dicts=term_dicts,
        book_type=book_type,
        youtube_video_id=yt_video_id,
        srt_cues=srt_cues,
        srt_cues_json=book.srt_data or "[]",
        srt_words_json="[]",
        video_current_pos=book.video_current_pos or 0,
    )


def _find_book(bookid):
    "Find book from db."
    br = BookRepository(db.session)
    return br.find(bookid)


@bp.route("/<int:bookid>", methods=["GET"])
def read(bookid):
    """
    Read a book, opening to its current page.

    This is called from the book listing, on Lute index.
    """
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)

    page_num = 1
    if not book.texts:
        flash(f"Book {book.title} has no pages (possibly the parser failed to split text).")
        return redirect("/", 302)

    text = book.texts[0]
    if book.current_tx_id:
        text = db.session.get(Text, book.current_tx_id)
        if text is None or text.bk_id != book.id:
            # Stored current_tx_id points to a non-existent / wrong Text
            # (e.g. pages were regenerated with a different parser).
            # Fall back to the first page.
            text = book.texts[0]
        page_num = text.order

    return _render_book_page(book, page_num)


@bp.route("/<int:bookid>/page/<int:pagenum>", methods=["GET"])
def read_page(bookid, pagenum):
    """
    Read a particular page of a book.
    """
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)

    pagenum = book.page_in_range(pagenum)
    return _render_book_page(book, pagenum)


@bp.route("/<int:bookid>/peek/<int:pagenum>", methods=["GET"])
def peek_page(bookid, pagenum):
    """
    Peek at a page; i.e. render it, but don't set the current text or start date.
    """
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)

    pagenum = book.page_in_range(pagenum)
    return _render_book_page(book, pagenum, track_page_open=False)


@bp.route("/page_done", methods=["post"])
def page_done():
    "Handle POST when page is done."
    data = request.json
    bookid = int(data.get("bookid"))
    pagenum = int(data.get("pagenum"))
    restknown = data.get("restknown")

    service = Service(db.session)
    service.mark_page_read(bookid, pagenum, restknown)
    return jsonify("ok")


@bp.route("/delete_page/<int:bookid>/<int:pagenum>", methods=["GET"])
def delete_page(bookid, pagenum):
    """
    Delete page.
    """
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)

    if len(book.texts) == 1:
        flash("Cannot delete only page in book.")
    else:
        book.remove_page(pagenum)
        db.session.add(book)
        db.session.commit()

    url = f"/read/{bookid}/page/{pagenum}"
    return redirect(url, 302)


@bp.route("/new_page/<int:bookid>/<position>/<int:pagenum>", methods=["GET", "POST"])
def new_page(bookid, position, pagenum):
    "Create a new page."
    form = TextForm()
    book = _find_book(bookid)

    if form.validate_on_submit():
        t = None
        if position == "before":
            t = book.add_page_before(pagenum)
        else:
            t = book.add_page_after(pagenum)
        t.book = book
        t.text = form.text.data
        db.session.add(book)
        db.session.commit()

        book.current_tx_id = t.id
        db.session.add(book)
        db.session.commit()

        return redirect(f"/read/{book.id}", 302)

    text_dir = "rtl" if book.language.right_to_left else "ltr"
    return render_template(
        "read/page_edit_form.html", hide_top_menu=True, form=form, text_dir=text_dir
    )


@bp.route("/save_player_data", methods=["post"])
def save_player_data():
    "Save current player position, bookmarks.  Called on a loop by the player."
    data = request.json
    bookid = int(data.get("bookid"))
    book = _find_book(bookid)
    book.audio_current_pos = float(data.get("position"))
    book.audio_bookmarks = data.get("bookmarks")
    db.session.add(book)
    db.session.commit()
    return jsonify("ok")


@bp.route("/save_youtube_player_data", methods=["post"])
def save_youtube_player_data():
    "Save current YouTube video position.  Called on a loop by the player."
    data = request.json
    bookid = int(data.get("bookid"))
    book = _find_book(bookid)
    book.video_current_pos = float(data.get("position", 0))
    db.session.add(book)
    db.session.commit()
    return jsonify("ok")


@bp.route("/youtube_subtitle_words/<int:bookid>", methods=["GET"])
def youtube_subtitle_words(bookid):
    """Return tokenized word HTML for every subtitle cue as JSON.

    Called lazily by the YouTube player after the page loads, so the
    expensive tokenization doesn't block the initial render.
    Results are cached per book (see _yt_subtitle_words_cache).
    """
    book = _find_book(bookid)
    if book is None:
        return jsonify([])
    words = _subtitle_words_html(book)
    return jsonify(words)


@bp.route("/start_reading/<int:bookid>/<int:pagenum>", methods=["GET"])
def start_reading(bookid, pagenum):
    "Called by ajax.  Update the text.start_date, and render page."
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)
    service = Service(db.session)
    paragraphs = service.start_reading(book, pagenum)
    return render_template("read/page_content.html", paragraphs=paragraphs)


@bp.route("/update_start_date/<int:bookid>/<int:pagenum>", methods=["GET", "POST"])
def update_start_date(bookid, pagenum):
    "Lightweight update of text.start_date, called by sendBeacon/beforeunload."
    book = _find_book(bookid)
    if book is None:
        return ""
    service = Service(db.session)
    service.update_start_date(book, pagenum)
    return ""


@bp.route("/refresh_page/<int:bookid>/<int:pagenum>", methods=["GET"])
def refresh_page(bookid, pagenum):
    "Refreshes the page content, but doesn't set the text's start_date."
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)
    service = Service(db.session)
    paragraphs = service.get_paragraphs(book, pagenum)
    return render_template("read/page_content.html", paragraphs=paragraphs)


@bp.route("/empty", methods=["GET"])
def empty():
    "Show an empty/blank page."
    return ""


@bp.route("/termform/<int:langid>/<text>", methods=["GET", "POST"])
def term_form(langid, text):
    """
    Create a multiword term for the given text, replacing the LUTESLASH hack.
    """
    usetext = text.replace("LUTESLASH", "/")
    repo = Repository(db.session)
    term = repo.find_or_new(langid, usetext)
    if term.status == 0:
        term.status = 1
    return handle_term_form(
        term,
        repo,
        db.session,
        "/read/term_edit_form.html",
        render_template("/read/updated.html", term_text=term.text),
        embedded_in_reading_frame=True,
    )


@bp.route("/edit_term/<int:term_id>", methods=["GET", "POST"])
def edit_term_form(term_id):
    """
    Edit a term.
    """
    repo = Repository(db.session)
    term = repo.load(term_id)
    # print(f"editing term {term_id}", flush=True)
    if term.status == 0:
        term.status = 1
    return handle_term_form(
        term,
        repo,
        db.session,
        "/read/term_edit_form.html",
        render_template("/read/updated.html", term_text=term.text),
        embedded_in_reading_frame=True,
    )


@bp.route("/term_bulk_edit_form", methods=["GET"])
def term_bulk_edit_form():
    """
    show_bulk_form
    """
    repo = Repository(db.session)
    return render_template(
        "read/term_bulk_edit_form.html",
        tags=repo.get_term_tags(),
    )


@bp.route("/termpopup/<int:termid>", methods=["GET"])
def term_popup(termid):
    """
    Get popup html for DBTerm, or None if nothing should be shown.
    """
    service = Service(db.session)
    d = service.get_popup_data(termid)
    if d is None:
        return ""
    return render_template(
        "read/termpopup.html",
        data=d,
    )


@bp.route("/flashcopied", methods=["GET"])
def flashcopied():
    return render_template("read/flashcopied.html")


@bp.route("/editpage/<int:bookid>/<int:pagenum>", methods=["GET", "POST"])
def edit_page(bookid, pagenum):
    "Edit the text on a page."
    book = _find_book(bookid)
    text = book.text_at_page(pagenum)
    if text is None:
        return redirect("/", 302)
    form = TextForm(obj=text)

    if form.validate_on_submit():
        form.populate_obj(text)
        db.session.add(text)
        db.session.commit()
        return redirect(f"/read/{book.id}", 302)

    text_dir = "rtl" if book.language.right_to_left else "ltr"
    return render_template(
        "read/page_edit_form.html", hide_top_menu=True, form=form, text_dir=text_dir
    )
