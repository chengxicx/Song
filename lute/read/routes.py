"""
/read endpoints.
"""

import json
from flask import Blueprint, flash, request, render_template, redirect, jsonify, Response, url_for
from lute.read.service import Service
from lute.read.render.service import Service as RenderService
from lute.read.forms import TextForm
from lute.read import bilibili_stream
from lute.term.model import Repository
from lute.term.routes import handle_term_form
from lute.settings.current import current_settings
from lute.models.book import Text
from lute.models.repositories import BookRepository, LanguageRepository
from lute.models.term import Term
from lute.book.service import (
    youtube_video_id,
    bilibili_embed_url,
    bilibili_video_id,
    bilibili_page,
)
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
    if (book.book_type or "") not in ("youtube", "bilibili", "mp3"):
        return []
    cache_key = (book.id, book.srt_data)
    cached = _yt_subtitle_words_cache.get(cache_key)
    if cached is not None and _subtitle_cache_is_fresh(cached):
        return cached["html"]
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

    # Record the term statuses that were baked into the rendered HTML so
    # later requests served by *other* gunicorn workers can detect when
    # the cache has gone stale (see _subtitle_cache_is_fresh).
    statuses = {
        ti.wo_id: ti.wo_status
        for chunk in chunks
        for ti in chunk
        if ti.wo_id is not None
    }
    _yt_subtitle_words_cache[cache_key] = {"html": result, "statuses": statuses}
    return result


def _subtitle_cache_is_fresh(entry):
    """True if the cached subtitle HTML still matches current term statuses.

    ``_yt_subtitle_words_cache`` is an in-memory per-gunicorn-worker dict.
    A status update only clears the cache of the worker that handled the
    POST, so other workers can keep serving stale ``data-status-class``
    values even after a full page reload.  Rather than relying on the
    in-memory invalidation to reach every worker, this re-reads the
    current statuses of the rendered word ids from the shared database and
    returns False (forcing a rebuild) when any of them differ from what
    was baked into the cached HTML.
    """
    statuses = entry["statuses"]
    if not statuses:
        return True
    wids = list(statuses.keys())
    rows = (
        db.session.query(Term.id, Term.status)
        .filter(Term.id.in_(wids))
        .all()
    )
    current = dict(rows)
    return all(current.get(wid) == status for wid, status in statuses.items())


def _sync_media_page_text_to_cues(book, original_text, new_text):
    """
    Propagate an edited page's text back into the subtitle cue texts.

    For media books (youtube / bilibili / mp3) the reading text is derived
    from the subtitle cues: each page is a contiguous run of cue lines, and
    the player's subtitles are rendered from ``book.cues`` (stored in
    ``book.srt_data``).  ``edit_page`` only updates the page's Text record,
    so without this the player would keep showing the old subtitle text.

    We locate the edited page's lines within the full cue line stream,
    then write the new lines back into the corresponding cues.  Only the
    common single-line-per-cue case is handled; if the alignment isn't
    clean (e.g. multi-line cues, or a different number of lines after the
    edit) we leave the cues untouched rather than risk corrupting them.

    Returns True if ``book.srt_data`` was updated, False otherwise.
    """
    if (book.book_type or "") not in ("youtube", "bilibili", "mp3"):
        return False
    cues = list(book.cues)
    if not cues:
        return False

    def _norm(s):
        # The page text may carry CRLF/CR line endings (or stray \r) while
        # cue texts split on "\n" only, so normalize before comparing.
        return (s or "").replace("\r", "")

    orig_lines = [_norm(x) for x in (original_text or "").split("\n")]
    new_lines = [_norm(x) for x in (new_text or "").split("\n")]
    if not orig_lines or not new_lines:
        return False

    # The full cue line stream is exactly how book.text is built
    # ("\n".join(cue text)).  Each line maps back to its owning cue, so
    # cues that themselves contain internal newlines stay aligned.
    full_lines = []
    line_to_cue = []
    for idx, cue in enumerate(cues):
        segs = _norm(cue.get("text") or "").split("\n")
        full_lines.extend(segs)
        line_to_cue.extend([idx] * len(segs))

    # Locate the edited page's lines by best alignment: pick the stream
    # offset whose block shares the most lines with the page, tolerating a
    # few lines that were already edited/drifted without giving up entirely.
    n = len(orig_lines)
    best_index = None
    best_score = -1
    for i in range(len(full_lines) - n + 1):
        score = sum(1 for k in range(n) if full_lines[i + k] == orig_lines[k])
        if score > best_score:
            best_score = score
            best_index = i
    if best_index is None or best_score <= 0:
        return False
    start = best_index

    covered = line_to_cue[start : start + n]
    cue_start = covered[0]
    # Only handle the clean single-line-cue case (the norm for subtitles),
    # where the covered cues are exactly one cue per line.
    if covered != list(range(cue_start, cue_start + n)):
        return False
    if len(new_lines) != n:
        return False

    changed = False
    for offset, line in enumerate(new_lines):
        k = cue_start + offset
        if cues[k]["text"] != line:
            cues[k]["text"] = line
            changed = True
    if not changed:
        return False

    book.srt_data = json.dumps(cues, ensure_ascii=False)
    invalidate_yt_subtitle_cache(book.id)
    return True


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
    bilibili_url = None
    bvid = None
    bilibili_page_num = 1
    if book_type == "bilibili":
        bilibili_url = bilibili_embed_url(book.source_uri)
        bvid, _aid = bilibili_video_id(book.source_uri)
        bilibili_page_num = bilibili_page(book.source_uri)
    srt_cues = []
    if book_type in ("youtube", "bilibili", "mp3"):
        srt_cues = list(book.cues)
        for c in srt_cues:
            c["start_str"] = _fmt_seconds(c.get("start", 0))
            c["end_str"] = _fmt_seconds(c.get("end", 0))
        # Subtitle word HTML is loaded lazily via AJAX
        # (/read/youtube_subtitle_words/<id>) to avoid blocking the
        # initial page render — tokenizing all cues can take 10-20s
        # for long videos.

    # Books with an audio file -- mp3-type books and legacy text books
    # that have an audio file attached -- stream through the useraudio
    # endpoint and are played by the unified media player.
    mp3_audio_url = None
    if book.audio_filename and book_type in ("mp3", ""):
        mp3_audio_url = f"/useraudio/stream/{book.id}"

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
        bilibili_url=bilibili_url,
        bilibili_bvid=bvid,
        bilibili_page_num=bilibili_page_num,
        mp3_audio_url=mp3_audio_url,
        srt_cues=srt_cues,
        srt_cues_json=book.srt_data or "[]",
        srt_words_json="[]",
        # For books that previously used the removed legacy audio player
        # the position was stored in audio_current_pos; fall back to it so
        # the unified player resumes from the same place.
        video_current_pos=book.video_current_pos or book.audio_current_pos or 0,
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


@bp.route("/bilibili/stream/mpd/<bvid>", methods=["GET"])
def bilibili_mpd(bvid):
    """Return the on-demand DASH manifest for a Bilibili video.

    The manifest's BaseURLs point at our own proxy endpoints so the
    browser never talks to Bilibili directly (which would be blocked by
    CORS / anti-leeching).  ``page`` selects a multi-part video page.
    """
    page = request.args.get("page", 1, type=int)
    try:
        info = bilibili_stream.stream_info(bvid, page)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    video_proxy = url_for(
        "read.bilibili_proxy", bvid=bvid, stream_type="video", page=page
    )
    audio_proxy = url_for(
        "read.bilibili_proxy", bvid=bvid, stream_type="audio", page=page
    )
    mpd = bilibili_stream.build_mpd(info, video_proxy, audio_proxy)
    return Response(mpd, mimetype="application/dash+xml")


@bp.route("/bilibili/stream/proxy/<bvid>/<stream_type>", methods=["GET"])
def bilibili_proxy(bvid, stream_type):
    """Proxy a range request for a Bilibili DASH segment to the CDN.

    ``stream_type`` is "video" or "audio".  Adds the Referer / UA headers
    the CDN requires and relays the byte range the player asked for.
    """
    if stream_type not in ("video", "audio"):
        return jsonify({"error": "invalid stream type"}), 400
    page = request.args.get("page", 1, type=int)
    range_header = request.headers.get("Range")
    try:
        info = bilibili_stream.stream_info(bvid, page)
        stream = info[stream_type]
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    status, headers, content = bilibili_stream.proxy_stream(
        stream["baseUrl"], range_header
    )
    return Response(content, status=status, headers=headers)


@bp.route("/youtube_subtitle_words/<int:bookid>", methods=["GET"])
def youtube_subtitle_words(bookid):
    """Return tokenized word HTML for every subtitle cue as JSON.

    Called lazily by the YouTube player after the page loads, so the
    expensive tokenization doesn't block the initial render.
    Results are cached per book (see _yt_subtitle_words_cache).
    """
    book = _find_book(bookid)
    if book is None:
        resp = jsonify([])
    else:
        words = _subtitle_words_html(book)
        resp = jsonify(words)

    # This JSON carries per-user term statuses that change on every status
    # update.  It is not text/html, so the app-wide no-store hook skips it,
    # and an origin/Cloudflare default (max-age) would otherwise cache it
    # for hours -- causing stale subtitle colors that a soft refresh can't
    # clear.  Force no-store so browser + CDN always re-fetch fresh data.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@bp.route("/start_reading/<int:bookid>/<int:pagenum>", methods=["GET"])
def start_reading(bookid, pagenum):
    "Called by ajax.  Update the text.start_date, and render page."
    book = _find_book(bookid)
    if book is None:
        flash(f"No book matching id {bookid}")
        return redirect("/", 302)
    service = Service(db.session)
    if (book.book_type or "") == "manga":
        ctx = service.manga_page_context(book, pagenum, True)
        if ctx is None:
            return render_template("read/page_content.html", paragraphs=[])
        return render_template("read/manga_page.html", **ctx)
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
    return render_page_fragment(book, pagenum, track_page_open=False)


def render_page_fragment(book, pagenum, track_page_open=False):
    """
    Render the reading-text fragment for a book page.

    Used by the /read/refresh_page route and by the HTMX status-update
    flow (term bulk_update_status returns this fragment for HX-Request
    calls) so the reading screen updates in a single round-trip.
    """
    service = Service(db.session)
    if (book.book_type or "") == "manga":
        ctx = service.manga_page_context(book, pagenum, track_page_open)
        if ctx is None:
            return render_template("read/page_content.html", paragraphs=[])
        return render_template("read/manga_page.html", **ctx)
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
    original_text = text.text
    form = TextForm(obj=text)

    if form.validate_on_submit():
        form.populate_obj(text)
        db.session.add(text)
        # For media books the reading text is driven by the subtitle cues;
        # propagate the edit back into the cues so the player subtitles
        # reflect the change.
        _sync_media_page_text_to_cues(book, original_text, text.text)
        db.session.commit()
        return redirect(f"/read/{book.id}", 302)

    text_dir = "rtl" if book.language.right_to_left else "ltr"
    return render_template(
        "read/page_edit_form.html", hide_top_menu=True, form=form, text_dir=text_dir
    )
