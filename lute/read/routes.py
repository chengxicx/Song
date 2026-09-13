"""
/read endpoints.
"""

import gzip
import json
import math
import os
from flask import (
    Blueprint,
    current_app,
    flash,
    request,
    render_template,
    redirect,
    jsonify,
    Response,
    url_for,
)
from lute.read.service import Service
from lute.read.render.service import Service as RenderService
from lute.read.forms import TextForm
from lute.read import bilibili_stream
from lute.term.model import Repository
from lute.term.routes import handle_term_form, serialize_term_form_data
from lute.settings.current import current_settings
from lute.multiuser.context import current_scope_key
from lute.models.book import Text
from lute.models.repositories import BookRepository, LanguageRepository
from lute.models.term import Term
from lute.book.service import (
    youtube_video_id,
    bilibili_embed_url,
    bilibili_video_id,
    bilibili_page,
    media_audio_url,
)
from lute.tts.routes import get_lang_code_for
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


def _subtitle_cache_key(book_id, srt_data):
    "Cache key scoped per user: book ids differ between user dbs."
    return (current_scope_key(), book_id, srt_data)


def invalidate_yt_subtitle_cache(book_id=None):
    """Clear the subtitle word-HTML cache.

    Called after term status updates so the subtitle re-renders with
    fresh data-status-class values.  If book_id is given, only the
    current user's entry for that book is cleared; otherwise the
    entire cache is wiped.
    """
    if book_id is not None:
        scope = current_scope_key()
        for k in [
            k for k in _yt_subtitle_words_cache if k[0] == scope and k[1] == book_id
        ]:
            _yt_subtitle_words_cache.pop(k, None)
    else:
        _yt_subtitle_words_cache.clear()


def patch_yt_subtitle_caches_for_term(texts):
    """
    Re-render cues containing any of *texts* in every cached book.

    Called after a single-word term save.  Such a save doesn't change
    how texts tokenize, so patching the rendered HTML in place keeps
    every book's cache correct without the full-book re-tokenization
    (10-20s for long books) that invalidation would force on the next
    fetch.  Multiword terms still need invalidate_yt_subtitle_cache().
    """
    needles = [(t or "").replace("\u200b", "").strip().lower() for t in (texts or [])]
    needles = [n for n in needles if n]
    if not needles:
        return
    br = BookRepository(db.session)
    scope = current_scope_key()
    for entry_scope, book_id, srt_data in list(_yt_subtitle_words_cache.keys()):
        if entry_scope != scope:
            # Another user's cached book: not visible from this db.
            continue
        haystack = (srt_data or "").lower()
        if not any(n in haystack for n in needles):
            continue
        book = br.find(book_id)
        if book is None:
            continue
        cues = list(book.cues)
        indices = []
        for n in needles:
            indices.extend(_cue_indices_matching_term(cues, n))
        _rerender_subtitle_cues(book, indices)


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
    if (book.book_type or "") not in ("youtube", "bilibili", "mp3", "netease", "video"):
        return []
    cache_key = _subtitle_cache_key(book.id, book.srt_data)
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
    _save_new_subtitle_terms(textitems)

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


def _save_new_subtitle_terms(textitems):
    "Save status-0 terms created while tokenizing subtitle text."
    new_terms = [
        ti.term
        for ti in textitems
        if ti.is_word
        and ti.term is not None
        and ti.term.id is None
        and ti.term.status == 0
    ]
    if new_terms:
        for t in new_terms:
            db.session.add(t)
        db.session.commit()


def _cue_indices_matching_term(cues, term_text):
    """
    Indices of cues whose text contains term_text (case-insensitive).

    Multiword term text carries zero-width spaces between tokens, which
    the raw cue text never has, so they are stripped before matching.
    Over-matching (a cue containing a longer word) is harmless: those
    cues simply get re-rendered to identical HTML.
    """
    ZWS = "\u200b"
    needle = (term_text or "").replace(ZWS, "").strip().lower()
    if not needle:
        return []
    return [i for i, c in enumerate(cues) if needle in (c.get("text") or "").lower()]


def _rerender_subtitle_cues(book, indices):
    """
    Re-render the given cue indices and patch the cached entry in place,
    so a term save only re-tokenizes the affected cues instead of the
    whole book.  Returns {cue_index: html}.

    If no cached entry exists for the book yet, the cues are still
    rendered fresh but the cache is left untouched -- writing a partial
    list would corrupt the full-render path, which expects html aligned
    with every cue.
    """
    cues = list(book.cues)
    valid = sorted({i for i in indices if 0 <= i < len(cues)})
    if not valid:
        return {}
    lang = book.language
    render_service = RenderService(db.session)
    cache_key = _subtitle_cache_key(book.id, book.srt_data)
    cached = _yt_subtitle_words_cache.get(cache_key)
    result = {}
    for i in valid:
        cue_text = (cues[i].get("text") or "").replace("\n", " ")
        textitems = render_service.get_textitems(cue_text, lang)
        _save_new_subtitle_terms(textitems)
        parts = []
        for ti in textitems:
            if ti.text == "¶":
                continue
            ti.sentence_number = i + 1
            parts.append(render_template("read/textitem.html", item=ti))
        html = "".join(parts)
        result[i] = html
        if cached is not None:
            cached["html"][i] = html
            for ti in textitems:
                if ti.wo_id is not None:
                    cached["statuses"][ti.wo_id] = ti.wo_status
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
    rows = db.session.query(Term.id, Term.status).filter(Term.id.in_(wids)).all()
    current = dict(rows)
    return all(current.get(wid) == status for wid, status in statuses.items())


def _sync_media_page_text_to_cues(book, pagenum, original_text, new_text):
    """
    Propagate an edited page's text back into the subtitle cue texts.

    For media books (youtube / bilibili / mp3) the reading text is derived
    from the subtitle cues: each page is a contiguous run of cue lines, and
    the player's subtitles are rendered from ``book.cues`` (stored in
    ``book.srt_data``).  ``edit_page`` only updates the page's Text record,
    so without this the player would keep showing the old subtitle text.

    The page is *anchored to its own position* in the cue line stream -- the
    cumulative line count of the pages before it -- and its lines are
    written to exactly those cues.  Anchoring matters.  Locating the page by
    best content match *anywhere* in the book is ambiguous: a page whose
    text has drifted by one line (e.g. two lines were merged, so the page
    has one line fewer than the cues it covers) matches a neighbouring run
    of cues much better than its own, and writing the page there silently
    shifts/duplicates whole subtitles.  Subtitle sync for book 270 was
    destroyed exactly that way.

    Only the clean single-line-per-cue case is handled.  When anything
    doesn't line up (a line was added/removed/merged, multi-line cues, or
    the page text has drifted from the cues) the cues are left untouched
    and the caller is told, rather than guessing and corrupting them.

    Returns "updated" (srt_data written), "unchanged" (nothing to do), or
    "mismatch" (page text and cues don't line up; cues left alone).
    """
    if (book.book_type or "") not in ("youtube", "bilibili", "mp3", "netease", "video"):
        return "unchanged"
    cues = list(book.cues)
    if not cues:
        return "unchanged"

    def _norm(s):
        # The page text may carry CRLF/CR line endings (or stray \r) while
        # cue texts split on "\n" only, so normalize before comparing.
        return (s or "").replace("\r", "")

    orig_lines = [_norm(x) for x in (original_text or "").split("\n")]
    new_lines = [_norm(x) for x in (new_text or "").split("\n")]
    if not orig_lines or not new_lines:
        return "unchanged"

    # The full cue line stream is exactly how book.text is built
    # ("\n".join(cue text)).  Each line maps back to its owning cue, so
    # cues that themselves contain internal newlines stay aligned.
    full_lines = []
    line_to_cue = []
    for idx, cue in enumerate(cues):
        segs = _norm(cue.get("text") or "").split("\n")
        full_lines.extend(segs)
        line_to_cue.extend([idx] * len(segs))

    n = len(orig_lines)
    # A changed line count means the page no longer maps one line per cue
    # (a line was added, removed, merged or split); don't guess.
    if len(new_lines) != n:
        return "mismatch"

    # The page's first line sits at the cumulative line count of the pages
    # before it: pages are contiguous runs of "\n".join(cue text).
    anchor = 0
    for t in book.texts:
        if t.order < pagenum:
            anchor += len(_norm(t.text).split("\n"))
    if anchor + n > len(full_lines):
        return "mismatch"

    covered = line_to_cue[anchor : anchor + n]
    cue_start = covered[0]
    # Only handle the clean single-line-cue case (the norm for subtitles),
    # where the covered cues are exactly one cue per line.
    if covered != list(range(cue_start, cue_start + n)):
        return "mismatch"

    # The page text must still agree with the cues it claims to cover: a
    # couple of drifted lines are tolerated (users fix lines out of band),
    # but a wholesale mismatch means the text and the subtitles have already
    # drifted apart, and writing here would corrupt the cues.
    score = sum(1 for k in range(n) if full_lines[anchor + k] == orig_lines[k])
    if score < n - 2:
        return "mismatch"

    changed = False
    for offset, line in enumerate(new_lines):
        k = cue_start + offset
        if cues[k]["text"] != line:
            cues[k]["text"] = line
            changed = True
    if not changed:
        return "unchanged"

    book.srt_data = json.dumps(cues, ensure_ascii=False)
    invalidate_yt_subtitle_cache(book.id)
    return "updated"


def _page_cue_span(book, pagenum, line_count):
    """
    Absolute cue indices covered by a media book page's lines, or None
    when the page and the cues don't line up.

    Same positional anchoring as _sync_media_page_text_to_cues: the
    page's first line sits at the cumulative line count of the pages
    before it, and only the clean one-line-per-cue case is handled (a
    page whose lines straddle a multi-line cue has no contiguous cue
    span, so the timing panel is not offered for it).
    """
    if (book.book_type or "") not in ("youtube", "bilibili", "mp3", "netease", "video"):
        return None
    cues = list(book.cues)
    if not cues or not line_count:
        return None

    def _norm(s):
        return (s or "").replace("\r", "")

    line_to_cue = []
    for idx, cue in enumerate(cues):
        segs = _norm(cue.get("text") or "").split("\n")
        line_to_cue.extend([idx] * len(segs))

    anchor = 0
    for t in book.texts:
        if t.order < pagenum:
            anchor += len(_norm(t.text).split("\n"))
    if anchor + line_count > len(line_to_cue):
        return None
    covered = line_to_cue[anchor : anchor + line_count]
    if covered != list(range(covered[0], covered[0] + line_count)):
        return None
    return covered


def _parse_cue_data(raw):
    """
    Parse the timing panel's submitted cue JSON (a list of
    {i, start, end, text} objects); None when absent or invalid, which
    falls back to the legacy plain-text sync.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    items = [d for d in data if isinstance(d, dict)]
    return items or None


def _apply_media_page_cue_data(book, pagenum, new_text, cue_data):
    """
    Write the timing panel's structured cue edits (text + start/end) back
    into book.cues, keyed by absolute cue index.

    Same anchoring rules as _sync_media_page_text_to_cues: the page must
    still be a contiguous one-line-per-cue run, or nothing is written and
    the caller flashes the mismatch warning.  The text-drift tolerance
    check is deliberately not applied: the panel is populated from the
    cues themselves, so its values stay authoritative even when the page
    text has drifted from them.

    Returns "updated", "unchanged", or "mismatch" (same meanings as
    _sync_media_page_text_to_cues).
    """
    new_lines = (new_text or "").replace("\r", "").split("\n")
    span = _page_cue_span(book, pagenum, len(new_lines))
    if span is None:
        return "mismatch"
    allowed = set(span)
    cues = list(book.cues)
    changed = False
    for item in cue_data:
        try:
            i = int(item["i"])
            if i not in allowed:
                continue
            start = float(item["start"])
            end = float(item["end"])
            txt = str(item.get("text") or "")
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end) and start >= 0 and end >= 0):
            continue
        cue = cues[i]
        if cue.get("start") != start:
            cue["start"] = start
            changed = True
        if cue.get("end") != end:
            cue["end"] = end
            changed = True
        if cue.get("text") != txt:
            cue["text"] = txt
            changed = True
    if not changed:
        return "unchanged"
    book.srt_data = json.dumps(cues, ensure_ascii=False)
    invalidate_yt_subtitle_cache(book.id)
    return "updated"


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
    show_highlights = current_settings()["show_highlights"]
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
    if book_type in ("youtube", "bilibili", "mp3", "netease", "video"):
        srt_cues = list(book.cues)
        for c in srt_cues:
            c["start_str"] = _fmt_seconds(c.get("start", 0))
            c["end_str"] = _fmt_seconds(c.get("end", 0))
        # Subtitle word HTML is loaded lazily via AJAX
        # (/read/youtube_subtitle_words/<id>) to avoid blocking the
        # initial page render — tokenizing all cues can take 10-20s
        # for long videos.

    # Books with a media file -- mp3-type books, legacy text books with
    # an audio file, and online "video" books -- play through the unified
    # media player.  Locally-stored files stream via /useraudio/stream;
    # a "video" book whose media was not downloaded (< 20 MB rule) plays
    # directly from its remote media_url.
    #
    # The stream URL is versioned with the file's mtime so the browser
    # can cache the audio (private, max-age) without ever serving a
    # stale file: replacing the audio changes its mtime, which changes
    # the URL, which bypasses the old cache entry.
    def _versioned_audio_url():
        fname = os.path.join(current_app.env_config.useraudiopath, book.audio_filename)
        try:
            version = int(os.stat(fname).st_mtime)
        except OSError:
            version = 0
        return f"/useraudio/stream/{book.id}?v={version}"

    mp3_audio_url = None
    if book_type == "video":
        if book.audio_filename:
            mp3_audio_url = _versioned_audio_url()
        elif book.media_url:
            mp3_audio_url = book.media_url
    elif book.audio_filename and book_type in ("mp3", "netease", ""):
        mp3_audio_url = _versioned_audio_url()

    # The unified player backend: youtube = iframe, video = HTML5 video,
    # audio = HTML5 audio.  Bilibili books use their own template.
    media_backend = (
        "youtube"
        if book_type == "youtube"
        else ("video" if book_type == "video" else "audio")
    )

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
        lang_code=get_lang_code_for(lang),
        translate_target_lang=(lang.translate_target_lang or ""),
        track_page_open=track_page_open,
        term_dicts=term_dicts,
        book_type=book_type,
        youtube_video_id=yt_video_id,
        bilibili_url=bilibili_url,
        bilibili_bvid=bvid,
        bilibili_page_num=bilibili_page_num,
        mp3_audio_url=mp3_audio_url,
        media_backend=media_backend,
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
        flash(
            f"Book {book.title} has no pages (possibly the parser failed to split text)."
        )
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
    allpages = bool(data.get("allpages", False))

    service = Service(db.session)
    service.mark_page_read(bookid, pagenum, restknown, allpages)
    return jsonify("ok")


@bp.route("/screen_done", methods=["post"])
def screen_done():
    "Handle POST when a reading sub-screen is finished."
    data = request.json
    bookid = int(data.get("bookid"))
    wordids = data.get("wordids") or []

    br = BookRepository(db.session)
    book = br.find(bookid)

    service = Service(db.session)
    count = service.set_terms_to_known(wordids, book)
    return jsonify({"updated": count})


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


def _gzip_if_accepted(resp):
    """
    gzip the response body when the client advertises support.

    The full-book subtitle word JSON can run to several MB of highly
    repetitive HTML; gzipping it cuts the transfer to a fraction, which
    matters when the server's uplink also carries the audio stream.
    Only applied to this JSON endpoint -- never to the audio/video
    streams, which are already-compressed media.
    """
    accept = request.headers.get("Accept-Encoding", "")
    encodings = [e.strip().split(";")[0].lower() for e in accept.split(",")]
    if "gzip" not in encodings or resp.headers.get("Content-Encoding"):
        return resp
    body = gzip.compress(resp.get_data(), compresslevel=5)
    resp.set_data(body)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(body))
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


@bp.route("/youtube_subtitle_words/<int:bookid>", methods=["GET"])
def youtube_subtitle_words(bookid):
    """Return tokenized word HTML for every subtitle cue as JSON.

    Called lazily by the YouTube player after the page loads, so the
    expensive tokenization doesn't block the initial render.
    Results are cached per book (see _yt_subtitle_words_cache).

    With ``?term=<text>`` (or ``?cue=<index>``) only the affected cues
    are re-rendered and the cached entry is patched in place; the
    response is then {"cues": {cue_index: html}, "patched": bool}
    instead of a full list.  This keeps term saves cheap: no full-book
    re-tokenization and no multi-megabyte payload while the audio is
    playing.  ``patched`` is false when no cached entry existed, telling
    the player its WORDS copy may be wholly out of sync (e.g. after the
    invalidation from a multiword-term save) and it should do a full
    refresh once the media is paused.
    """
    book = _find_book(bookid)
    term_text = request.args.get("term")
    cue_arg = request.args.get("cue")
    if book is not None and (term_text is not None or cue_arg is not None):
        cues = list(book.cues)
        if term_text is not None:
            indices = _cue_indices_matching_term(cues, term_text)
        elif cue_arg.isdigit():
            indices = [int(cue_arg)]
        else:
            indices = []
        patched = (
            _subtitle_cache_key(book.id, book.srt_data) in _yt_subtitle_words_cache
        )
        result = _rerender_subtitle_cues(book, indices)
        resp = jsonify(
            {
                "cues": {str(i): h for i, h in result.items()},
                "patched": patched,
            }
        )
    elif book is None:
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
    return _gzip_if_accepted(resp)


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
    if (book.book_type or "") == "pdf":
        ctx = service.pdf_page_context(book, pagenum, True)
        if ctx is None:
            return render_template("read/page_content.html", paragraphs=[])
        return render_template("read/pdf_page.html", **ctx)
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
    if (book.book_type or "") == "pdf":
        ctx = service.pdf_page_context(book, pagenum, track_page_open)
        if ctx is None:
            return render_template("read/page_content.html", paragraphs=[])
        return render_template("read/pdf_page.html", **ctx)
    paragraphs = service.get_paragraphs(book, pagenum)
    return render_template("read/page_content.html", paragraphs=paragraphs)


@bp.route("/empty", methods=["GET"])
def empty():
    "Show an empty/blank page."
    return ""


def _term_form_action(term):
    "The URL a term form POST goes back to, for the given term."
    if term.id:
        return f"/read/edit_term/{term.id}"
    sendtext = (term.original_text or term.text or "").replace("/", "LUTESLASH")
    return f"/read/termform/{term.language_id}/{sendtext}"


def _term_form_json_or_form(term, repo, form_template_name, embedded=True):
    "GET ?format=json returns the form's data; otherwise render the form page."
    if request.args.get("format") == "json":
        data = serialize_term_form_data(term, repo, db.session, _term_form_action(term))
        return jsonify(data)
    return handle_term_form(
        term,
        repo,
        db.session,
        form_template_name,
        lambda: _term_form_saved_response(term),
        embedded_in_reading_frame=embedded,
    )


def _term_form_saved_response(term):
    "Response after a term form post succeeds, AJAX or regular."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"status": "ok", "term_id": term.id, "term_text": term.text})
    return render_template("/read/updated.html", term_text=term.text)


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
    return _term_form_json_or_form(term, repo, "/read/term_edit_form.html")


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
    return _term_form_json_or_form(term, repo, "/read/term_edit_form.html")


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
        # For media books the reading text is driven by the subtitle cues.
        # The page's timing panel (when shown) submits structured cue
        # edits (text + start/end times) by absolute cue index; without
        # it, fall back to propagating the plain text edits into the cue
        # texts so the player subtitles reflect the change.
        cue_data = _parse_cue_data(request.form.get("cue_data"))
        if cue_data is not None:
            status = _apply_media_page_cue_data(book, pagenum, text.text, cue_data)
        else:
            status = _sync_media_page_text_to_cues(
                book, pagenum, original_text, text.text
            )
        db.session.commit()
        if status == "mismatch":
            flash(
                "Saved, but the subtitles were not updated: this page no "
                "longer has the same number of lines as the subtitle cues "
                "it covers (a line was added, removed, merged or split), or "
                "its text has drifted from the subtitles.  Keep one line "
                "per subtitle line, or re-import the subtitle file.",
                "notice",
            )
        return redirect(f"/read/{book.id}", 302)

    text_dir = "rtl" if book.language.right_to_left else "ltr"
    # Lyrics timing panel: the page's cues (with absolute indices) and the
    # book's audio stream, so the panel can offer per-line timing edits.
    page_cues = []
    cue_audio_url = None
    page_lines = (original_text or "").replace("\r", "").split("\n")
    span = _page_cue_span(book, pagenum, len(page_lines))
    if span is not None:
        page_cues = [
            {
                "i": i,
                "start": book.cues[i].get("start", 0),
                "end": book.cues[i].get("end", 0),
                "text": book.cues[i].get("text") or "",
            }
            for i in span
        ]
        cue_audio_url = media_audio_url(book)
    return render_template(
        "read/page_edit_form.html",
        hide_top_menu=True,
        form=form,
        text_dir=text_dir,
        page_cues=page_cues,
        cue_audio_url=cue_audio_url,
    )
