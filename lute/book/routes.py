"""
/book routes.
"""

import json
import os
import urllib.parse
from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    flash,
    current_app,
)
from lute.utils.data_tables import DataTablesFlaskParamParser
from lute.book.service import (
    Service as BookService,
    BookImportException,
    BookDataFromUrl,
    parse_subtitle_file,
    parse_subtitle_from_url,
    cues_to_srt_text,
    youtube_video_id,
    bilibili_video_id,
    _url_content_length,
    download_url_to_file,
    MEDIA_LOCAL_MAX_BYTES,
)
from lute.book.datatables import get_data_tables_list
from lute.book.forms import NewBookForm, EditBookForm, ALLOWED_AUDIO_EXTENSIONS
from lute.book.stats import Service as StatsService
from lute.book.stats import get_difficulty_label
import lute.utils.formutils
from lute.db import db
from lute.models.language import Language
from lute.models.repositories import (
    BookRepository,
    UserSettingRepository,
    LanguageRepository,
)
from lute.book.model import Book, Repository


bp = Blueprint("book", __name__, url_prefix="/book")


def _load_term_custom_filters(request_form, parameters):
    "Manually add filters that the DataTablesFlaskParamParser doesn't know about."
    filter_param_names = [
        "filtLanguage",
        "filtTag",
        "filtNewWord",
    ]
    request_params = request_form.to_dict(flat=True)
    for p in filter_param_names:
        parameters[p] = request_params.get(p)


def datatables_source(is_archived):
    "Get datatables json for books."
    # In the future, we might want to create an API such as
    # get_books(sort_order, search_string, length, index, language_id).
    # See DataTablesFlaskParamParser.parse_params_2(request.form)
    # (currently unused)
    parameters = DataTablesFlaskParamParser.parse_params(request.form)
    _load_term_custom_filters(request.form, parameters)
    data = get_data_tables_list(parameters, is_archived, db.session)
    return jsonify(data)


@bp.route("/datatables/active", methods=["POST"])
def datatables_active_source():
    "Datatables data for active books."
    return datatables_source(False)


@bp.route("/archived", methods=["GET"])
def archived():
    "List archived books."
    language_choices = lute.utils.formutils.language_choices(
        db.session, "(all languages)"
    )
    current_language_id = lute.utils.formutils.valid_current_language_id(db.session)

    return render_template(
        "book/index.html",
        status="Archived",
        language_choices=language_choices,
        current_language_id=current_language_id,
    )


# Archived must be capitalized, or the ajax call 404's.
@bp.route("/datatables/Archived", methods=["POST"])
def datatables_archived_source():
    "Datatables data for archived books."
    return datatables_source(True)


def _book_from_url(url):
    "Get data for a new book, or flash an error if can't parse."
    service = BookService()
    bd = None
    try:
        bd = service.book_data_from_url(url)
    except BookImportException as e:
        flash(e.message, "notice")
        bd = BookDataFromUrl()
    b = Book()
    b.title = bd.title
    b.source_uri = bd.source_uri
    b.text = bd.text
    return b


def _language_is_rtl_map():
    """
    Return language-id to is_rtl map, to be used during book creation.
    """
    ret = {}
    for lang in db.session.query(Language).all():
        ret[lang.id] = lang.right_to_left
    return ret


@bp.route("/new", methods=["GET", "POST"])
def new():
    "Create a new book, either from text or from a file."
    b = Book()
    import_url = request.args.get("importurl", "").strip()
    if import_url != "":
        b = _book_from_url(import_url)

    form = NewBookForm(obj=b)
    form.language_id.choices = lute.utils.formutils.language_choices(db.session)
    repo = Repository(db.session)

    if form.validate_on_submit():
        try:
            form.populate_obj(b)
            svc = BookService()
            book = svc.import_book(b, db.session)
            return redirect(f"/read/{book.id}/page/1", 302)
        except BookImportException as e:
            flash(e.message, "notice")

    # Don't set the current language before submit.
    usrepo = UserSettingRepository(db.session)
    current_language_id = int(usrepo.get_value("current_language_id"))
    requested_language_id = request.args.get("language_id")
    if requested_language_id:
        try:
            form.language_id.data = int(requested_language_id)
        except ValueError:
            form.language_id.data = current_language_id
    else:
        form.language_id.data = current_language_id

    return render_template(
        "book/create_new.html",
        book=b,
        form=form,
        tags=repo.get_book_tags(),
        rtl_map=json.dumps(_language_is_rtl_map()),
        show_language_selector=True,
        allowed_extensions=ALLOWED_AUDIO_EXTENSIONS,
    )


@bp.route("/edit/<int:bookid>", methods=["GET", "POST"])
def edit(bookid):
    "Edit a book - title, text, source, tags, and audio can be changed."
    repo = Repository(db.session)
    b = repo.load(bookid)
    form = EditBookForm(obj=b)

    # For youtube/bilibili/mp3/video books the text field holds the SRT
    # original (with timestamps) so it can be edited directly.
    if request.method == "GET" and (b.book_type or "") in (
        "youtube",
        "bilibili",
        "mp3",
        "video",
    ):
        form.text.data = cues_to_srt_text(b.cues)

    if form.validate_on_submit():
        try:
            form.populate_obj(b)
            svc = BookService()
            svc.import_book(b, db.session)
            flash(f"{b.title} updated.")
            return redirect("/", 302)
        except BookImportException as e:
            flash(e.message, "notice")

    lang_repo = LanguageRepository(db.session)
    lang = lang_repo.find(b.language_id)
    return render_template(
        "book/edit.html",
        book=b,
        title_direction="rtl" if lang.right_to_left else "ltr",
        form=form,
        tags=repo.get_book_tags(),
        allowed_extensions=ALLOWED_AUDIO_EXTENSIONS,
    )


@bp.route("/import_webpage", methods=["GET", "POST"])
def import_webpage():
    "Import a web page, a YouTube video, an MP3 audio, an online video, or subtitles."
    if request.method == "POST":
        import_type = request.form.get("import_type", "webpage")
        if import_type == "youtube":
            return _import_youtube_video()
        if import_type == "bilibili":
            return _import_bilibili_video()
        if import_type == "mp3":
            return _import_mp3_audio()
        if import_type == "video":
            return _import_online_video()
        if import_type == "manga":
            return _import_mokuro_manga()
        return _redirect_to_new_book_form()

    usrepo = UserSettingRepository(db.session)
    current_language_id = int(usrepo.get_value("current_language_id"))
    language_choices = lute.utils.formutils.language_choices(db.session)
    repo = Repository(db.session)
    existing_tags = repo.get_book_tags()
    return render_template(
        "book/import_webpage.html",
        language_choices=language_choices,
        current_language_id=current_language_id,
        existing_tags=existing_tags,
    )


def _parse_tagify_tags(raw, default_tag):
    """
    Parse a Tagify JSON value (e.g. '[{"value":"mp3"},{"value":"music"}]')
    into a list of non-empty tag strings.  Falls back to [default_tag]
    when the result is empty, so a book always has at least one tag.
    Also accepts a plain comma-separated string for backwards compat.
    """
    import json as _json

    tags = []
    raw = (raw or "").strip()
    if raw:
        # Tagify submits JSON; handle that first.
        if raw.startswith("["):
            try:
                items = _json.loads(raw)
                tags = [
                    (i.get("value") or "").strip()
                    for i in items
                    if isinstance(i, dict)
                ]
            except (ValueError, TypeError):
                pass
        else:
            # Plain comma- or space-separated string (legacy fallback).
            tags = [t.strip() for t in raw.replace(",", " ").split()]
    tags = [t for t in tags if t]
    if not tags:
        tags = [default_tag]
    return tags


def _redirect_to_new_book_form():
    "Redirect to the normal new-book form with the import url pre-filled."
    import_url = request.form.get("importurl", "").strip()
    url = "/book/new?importurl=" + urllib.parse.quote(import_url)
    language_id = request.form.get("language_id")
    if language_id:
        url += f"&language_id={language_id}"
    return redirect(url, 302)


def _resolve_remote_media(url):
    """
    Decide whether an online media URL should be stored locally.

    Per the import spec, video / audio from an online URL is downloaded
    and stored locally when it is under 20 MB; larger files are streamed
    directly from their remote URL.  Returns (audio_filename, media_url):
    exactly one is set.
    """
    size = _url_content_length(url)
    if size is not None and size > MEDIA_LOCAL_MAX_BYTES:
        return None, url
    try:
        fname = download_url_to_file(
            url,
            current_app.env_config.useraudiopath,
            max_bytes=MEDIA_LOCAL_MAX_BYTES,
        )
        return fname, None
    except BookImportException:
        # Too large to download, or the download failed; fall back to
        # streaming from the remote URL.
        return None, url


def _import_youtube_video():
    "Create a youtube book from the form data."
    url = request.form.get("youtube_url", "").strip()
    tags = _parse_tagify_tags(request.form.get("youtube_tag", ""), "youtube")
    language_id = request.form.get("language_id")
    srt_file = request.files.get("srt_file")
    

    if youtube_video_id(url) is None:
        flash("Please enter a valid YouTube video URL.", "notice")
        return redirect("/book/import_webpage", 302)

    if srt_file is None or srt_file.filename == "":
        flash("Please upload an SRT or VTT subtitle file.", "notice")
        return redirect("/book/import_webpage", 302)

    try:
        text, cues_json = parse_subtitle_file(
            srt_file.filename,
            srt_file.stream,
        )
    except Exception as e:  # pylint: disable=broad-except
        msg = f"Could not parse subtitle file {srt_file.filename} (error: {str(e)})"
        flash(msg, "notice")
        return redirect("/book/import_webpage", 302)

    if text.strip() == "":
        flash("The subtitle file contains no text.", "notice")
        return redirect("/book/import_webpage", 302)

    b = Book()
    b.language_id = int(language_id) if language_id else None
    b.title = BookService().youtube_title(url)
    b.source_uri = url
    b.text = text
    b.srt_data = cues_json
    b.book_type = "youtube"
    b.book_tags = tags
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"

    svc = BookService()
    try:
        book = svc.import_book(b, db.session)
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    return redirect(f"/read/{book.id}/page/1", 302)


def _import_bilibili_video():
    "Create a bilibili book from the form data."
    url = request.form.get("bilibili_url", "").strip()
    tags = _parse_tagify_tags(request.form.get("bilibili_tag", ""), "bilibili")
    language_id = request.form.get("language_id")
    srt_file = request.files.get("srt_file")
    

    bvid, aid = bilibili_video_id(url)
    if bvid is None and aid is None:
        flash("Please enter a valid Bilibili video URL.", "notice")
        return redirect("/book/import_webpage", 302)

    if srt_file is None or srt_file.filename == "":
        flash("Please upload an SRT or VTT subtitle file.", "notice")
        return redirect("/book/import_webpage", 302)

    try:
        text, cues_json = parse_subtitle_file(
            srt_file.filename,
            srt_file.stream,
        )
    except Exception as e:  # pylint: disable=broad-except
        msg = f"Could not parse subtitle file {srt_file.filename} (error: {str(e)})"
        flash(msg, "notice")
        return redirect("/book/import_webpage", 302)

    if text.strip() == "":
        flash("The subtitle file contains no text.", "notice")
        return redirect("/book/import_webpage", 302)

    b = Book()
    b.language_id = int(language_id) if language_id else None
    b.title = BookService().bilibili_title(url)
    b.source_uri = url
    b.text = text
    b.srt_data = cues_json
    b.book_type = "bilibili"
    b.book_tags = tags
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"

    svc = BookService()
    try:
        book = svc.import_book(b, db.session)
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    return redirect(f"/read/{book.id}/page/1", 302)


def _import_mp3_audio():
    "Create an audio book (mp3/m4a) from an uploaded file OR an online URL, plus subtitles."
    mp3_file = request.files.get("mp3_file")
    mp3_url = (request.form.get("mp3_url") or "").strip()
    srt_file = request.files.get("srt_file")
    srt_url = (request.form.get("mp3_srt_url") or "").strip()
    tags = _parse_tagify_tags(request.form.get("mp3_tag", ""), "mp3")
    language_id = request.form.get("language_id")
    title = (request.form.get("mp3_title") or "").strip()

    # --- Audio source: an uploaded file OR an online URL. ---
    audio_filename = None
    media_url = None
    source_uri = None
    if mp3_file and mp3_file.filename:
        fname = (mp3_file.filename or "").lower()
        if not fname.endswith((".mp3", ".m4a")):
            flash("Please upload a valid audio file (.mp3 or .m4a).", "notice")
            return redirect("/book/import_webpage", 302)
        source_uri = mp3_file.filename
        if not title:
            base = mp3_file.filename or "MP3 audio"
            title = ".".join(base.split(".")[:-1]) or base
    elif mp3_url:
        audio_filename, media_url = _resolve_remote_media(mp3_url)
        source_uri = mp3_url
        if not title:
            base = os.path.basename(urllib.parse.urlparse(mp3_url).path)
            title = base or "MP3 audio"
    else:
        flash("Please provide an audio file (upload or an online URL).", "notice")
        return redirect("/book/import_webpage", 302)

    # --- Subtitles: an uploaded file OR an online subtitle URL. ---
    try:
        if srt_file and srt_file.filename:
            text, cues_json = parse_subtitle_file(srt_file.filename, srt_file.stream)
        elif srt_url:
            text, cues_json = parse_subtitle_from_url(srt_url)
        else:
            text, cues_json = "", None
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    except Exception as e:  # pylint: disable=broad-except
        msg = f"Could not parse subtitle (error: {str(e)})"
        flash(msg, "notice")
        return redirect("/book/import_webpage", 302)

    if not (text and text.strip()):
        flash("Please provide subtitles (upload a file or an online URL).", "notice")
        return redirect("/book/import_webpage", 302)

    title = title[:200]

    b = Book()
    b.language_id = int(language_id) if language_id else None
    b.title = title
    b.source_uri = source_uri
    b.text = text
    b.srt_data = cues_json
    b.book_type = "mp3"
    b.book_tags = tags
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"

    svc = BookService()
    try:
        if mp3_file and mp3_file.filename:
            b.audio_stream = mp3_file.stream
            b.audio_stream_filename = mp3_file.filename
        elif audio_filename:
            b.audio_filename = audio_filename
        elif media_url:
            b.media_url = media_url
        book = svc.import_book(b, db.session)
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    return redirect(f"/read/{book.id}/page/1", 302)


def _import_online_video():
    """
    Create an online video book.

    The media is either an uploaded video file or an online video URL
    (< 20 MB is downloaded and stored locally).  The subtitles are
    either an uploaded file or an online subtitle URL (srt/vtt/txt), and
    are always downloaded to local.  Playback uses the unified media
    player (HTML5 <video>) with the subtitle cues, like the MP3 reader.
    """
    video_file = request.files.get("video_file")
    video_url = (request.form.get("video_url") or "").strip()
    srt_file = request.files.get("video_srt_file")
    srt_url = (request.form.get("video_srt_url") or "").strip()
    tags = _parse_tagify_tags(request.form.get("video_tag", ""), "video")
    language_id = request.form.get("language_id")
    title = (request.form.get("video_title") or "").strip()

    # --- Subtitles: uploaded file OR online subtitle URL (always local). ---
    try:
        if srt_file and srt_file.filename:
            text, cues_json = parse_subtitle_file(srt_file.filename, srt_file.stream)
        elif srt_url:
            text, cues_json = parse_subtitle_from_url(srt_url)
        else:
            text, cues_json = "", None
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    except Exception as e:  # pylint: disable=broad-except
        msg = f"Could not parse subtitle (error: {str(e)})"
        flash(msg, "notice")
        return redirect("/book/import_webpage", 302)

    if not (text and text.strip()):
        flash("Please provide subtitles (upload a file or an online URL).", "notice")
        return redirect("/book/import_webpage", 302)

    # --- Media: uploaded video file OR online video URL. ---
    source_uri = None
    audio_filename = None
    media_url = None
    svc = BookService()
    if video_file and video_file.filename:
        fname = (video_file.filename or "").lower()
        if not fname.endswith((".mp4", ".webm", ".mov", ".ogv", ".ogg", ".m4v")):
            flash(
                "Please upload a valid video file (.mp4, .webm, .mov, .ogv, .ogg).",
                "notice",
            )
            return redirect("/book/import_webpage", 302)
        audio_filename = svc.save_audio_file(video_file)
        source_uri = video_file.filename
        if not title:
            title = ".".join(video_file.filename.split(".")[:-1]) or video_file.filename
    elif video_url:
        audio_filename, media_url = _resolve_remote_media(video_url)
        source_uri = video_url
        if not title:
            base = os.path.basename(urllib.parse.urlparse(video_url).path)
            title = base or "Online video"
    else:
        flash("Please provide a video (upload a file or an online URL).", "notice")
        return redirect("/book/import_webpage", 302)

    title = title[:200]

    b = Book()
    b.language_id = int(language_id) if language_id else None
    b.title = title
    b.source_uri = source_uri
    b.text = text
    b.srt_data = cues_json
    b.book_type = "video"
    b.book_tags = tags
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"
    if audio_filename:
        b.audio_filename = audio_filename
    if media_url:
        b.media_url = media_url

    try:
        book = svc.import_book(b, db.session)
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    return redirect(f"/read/{book.id}/page/1", 302)


def _find_book(bookid):
    "Find book from db."
    br = BookRepository(db.session)
    return br.find(bookid)


def _import_mokuro_manga():
    "Create a Mokuro manga book from an uploaded .zip/.cbz archive."
    manga_file = request.files.get("manga_file")
    tags = _parse_tagify_tags(request.form.get("manga_tag", ""), "Manga")
    language_id = request.form.get("language_id")
    title = (request.form.get("manga_title") or "").strip()

    if manga_file is None or manga_file.filename == "":
        flash("Please upload a Mokuro manga archive (.zip or .cbz).", "notice")
        return redirect("/book/import_webpage", 302)

    fname = (manga_file.filename or "").lower()
    if not fname.endswith((".zip", ".cbz")):
        flash("Please upload a valid Mokuro manga archive (.zip or .cbz).", "notice")
        return redirect("/book/import_webpage", 302)

    if not title:
        base = manga_file.filename or "Mokuro manga"
        title = ".".join(base.split(".")[:-1]) or base
    title = title[:200]

    b = Book()
    b.language_id = int(language_id) if language_id else None
    b.title = title
    b.source_uri = manga_file.filename
    b.book_type = "manga"
    b.book_tags = tags
    b.threshold_page_tokens = 250
    b.split_by = "paragraphs"

    svc = BookService()
    try:
        b.manga_stream = manga_file.stream
        b.manga_stream_filename = manga_file.filename
        book = svc.import_book(b, db.session)
    except BookImportException as e:
        flash(e.message, "notice")
        return redirect("/book/import_webpage", 302)
    return redirect(f"/read/{book.id}/page/1", 302)


@bp.route("/archive/<int:bookid>", methods=["POST"])
def archive(bookid):
    "Archive a book."
    b = _find_book(bookid)
    b.archived = True
    db.session.add(b)
    db.session.commit()
    return redirect("/", 302)


@bp.route("/unarchive/<int:bookid>", methods=["POST"])
def unarchive(bookid):
    "Archive a book."
    b = _find_book(bookid)
    b.archived = False
    db.session.add(b)
    db.session.commit()
    return redirect("/", 302)


@bp.route("/delete/<int:bookid>", methods=["POST"])
def delete(bookid):
    "Archive a book."
    b = _find_book(bookid)
    db.session.delete(b)
    db.session.commit()
    return redirect("/", 302)


@bp.route("/table_stats/<int:bookid>", methods=["GET"])
def table_stats(bookid):
    "Get the stats, return ajax."
    b = _find_book(bookid)
    if b is None or b.language is None:
        # Playwright tests were sometimes passing an id that didn't exist ...
        # I believe this is due to page caching, i.e. the book listing
        # is showing books and IDs that no longer exist after cache reset.
        # TODO fix_hack: get rid of this hack.
        return jsonify({})
    svc = StatsService(db.session)
    stats = svc.get_stats(b)
    return jsonify(_stats_to_dict(stats))


def _stats_to_dict(stats):
    "Convert a BookStats object to the dict shape expected by the frontend."
    label, color, description = get_difficulty_label(stats.new_word_percent)
    return {
        "distinctterms": stats.distinctterms,
        "distinctunknowns": stats.distinctunknowns,
        "unknownpercent": stats.unknownpercent,
        "new_word_percent": stats.new_word_percent,
        "difficulty_label": label,
        "difficulty_color": color,
        "difficulty_description": description,
        "status_distribution": stats.status_distribution,
    }


@bp.route("/table_stats", methods=["POST"])
def table_stats_batch():
    """
    Get stats for a batch of books in one request.

    The frontend book listing previously issued one /table_stats/<id>
    request per visible row (25+ requests per page). This batches those
    into a single request to reduce DB connection-pool pressure and load.

    Defensive: roll back any broken session state (e.g. PendingRollbackError
    from a previous failed flush) before processing, so that subsequent
    queries like _find_book don't 500 the entire batch.
    """
    data = request.get_json(silent=True) or {}
    book_ids = data.get("book_ids") or []
    svc = StatsService(db.session)
    ret = {}
    for book_id in book_ids:
        try:
            b = _find_book(book_id)
        except Exception:  # noqa: BLE001
            db.session.rollback()
            continue
        if b is None or b.language is None:
            continue
        try:
            stats = svc.get_stats(b)
        except Exception:  # noqa: BLE001 - one bad book shouldn't fail the batch
            db.session.rollback()
            continue
        ret[book_id] = _stats_to_dict(stats)
    return jsonify(ret)
