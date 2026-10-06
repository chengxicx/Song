"""
/book/whisper routes: transcription (auto-subtitles) and model management.

Split out of lute.book.routes: the pure-JSON endpoints the import page
(/prepare), the edit page (/retranscribe, /status) and Settings ->
Whisper (/available, /install, /models, /download_model,
/delete_model) call.  The URLs are unchanged.
"""

import os
import urllib.parse
import uuid

from flask import Blueprint, current_app, jsonify, request

from lute.book.service import (
    BookImportException,
    MEDIA_LOCAL_MAX_BYTES,
    _url_content_length,
    download_url_to_file,
)
from lute.book.forms import (
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    AUDIO_VALIDATION_MSG,
    VIDEO_VALIDATION_MSG,
)
from lute.book import whisper_transcribe
from lute.book.routes import _find_book, _parse_tagify_tags
from lute.db import db
from lute.models.language import Language
from lute.multiuser.context import get_current_user
from lute.multiuser.permissions import admin_only_if_multiuser_json

bp = Blueprint("whisper", __name__, url_prefix="/book/whisper")


@bp.route("/available", methods=["GET"])
def whisper_available():
    "Whether the optional faster-whisper dependency is installed."
    return jsonify(whisper_transcribe.whisper_status())


@bp.route("/install", methods=["POST"])
@admin_only_if_multiuser_json
def whisper_install():
    """
    pip-install faster-whisper on demand.

    Answers JSON (not flash+redirect) so the import page keeps the
    files the user has already chosen.
    """
    ok, message = whisper_transcribe.install_whisper()
    return jsonify({"ok": ok, "message": message})


def _engine_error(model_size, language):
    """
    Why the picked transcription engine can't run for this language, or
    None when it can.  SenseVoice (sherpa-onnx) and faster-whisper are
    independent optional installs, so the availability gate follows the
    engine the form actually picked.  SenseVoice only covers
    zh/yue/en/ja/ko.
    """
    if model_size == "sensevoice":
        from lute.book import sensevoice

        if not sensevoice.installed():
            return (
                "SenseVoice isn't installed on this server yet "
                "-- set it up under Settings -> Whisper (auto-subtitles)."
            )
        if sensevoice.lang_code_for(language) is None:
            return (
                "SenseVoice transcribes Chinese, Cantonese, "
                "English, Japanese and Korean only -- pick a whisper "
                "model for this language."
            )
    elif not whisper_transcribe.whisper_status()["installed"]:
        return "faster-whisper is not installed yet."
    return None


# The media import forms that can hand their file/URL to the
# transcription queue.  The field names and the extension whitelist
# differ per type, so both the create-time auto-transcribe
# (/whisper/prepare) and the import page's JS read them from here.
TRANSCRIBE_IMPORT_TYPES = {
    "mp3": {
        "file": "mp3_file",
        "url": "mp3_url",
        "title": "mp3_title",
        "tag": "mp3_tag",
        "extensions": ALLOWED_AUDIO_EXTENSIONS,
        "extension_error": AUDIO_VALIDATION_MSG,
        "source_label": "audio",
        "fallback_title": "MP3 audio",
    },
    "video": {
        "file": "video_file",
        "url": "video_url",
        "title": "video_title",
        "tag": "video_tag",
        "extensions": ALLOWED_VIDEO_EXTENSIONS,
        "extension_error": VIDEO_VALIDATION_MSG,
        "source_label": "video",
        "fallback_title": "Online video",
    },
}

# Book types that may be re-transcribed from the edit page.
RETRANSCRIBE_BOOK_TYPES = tuple(TRANSCRIBE_IMPORT_TYPES.keys())


def _transcribe_import_spec(import_type):
    "Field names and extension rules for the media form being transcribed."
    return TRANSCRIBE_IMPORT_TYPES.get(import_type) or TRANSCRIBE_IMPORT_TYPES["mp3"]


@bp.route("/prepare", methods=["POST"])
def whisper_prepare():
    """
    Start a background transcription that creates an mp3 or video book.

    Takes the media form's fields (import_type, language_id, file or
    URL, title, tags) plus whisper_model ("sensevoice", or a whisper
    size).  The media is saved to a temp file within this request (the
    upload stream cannot outlive it), and a daemon thread transcribes it
    and imports the book; the page polls /whisper/status/<task_id> until
    the book id comes back.  import_type picks which form's field names
    are read and which book type is created ("mp3" -> HTML5 audio,
    "video" -> HTML5 video); it defaults to mp3.
    """
    if whisper_transcribe.has_running_task():
        return (
            jsonify(
                {
                    "error": "A transcription or model download is already "
                    "running -- please wait for it to finish."
                }
            ),
            409,
        )
    whisper_transcribe.purge_finished_tasks()

    language_id = request.form.get("language_id")
    if not language_id:
        return jsonify({"error": "Please choose a language."}), 400
    language = db.session.get(Language, int(language_id))
    if language is None:
        return jsonify({"error": "Please choose a valid language."}), 400

    import_type = (request.form.get("import_type") or "mp3").strip()
    spec = _transcribe_import_spec(import_type)
    book_type = "video" if import_type == "video" else "mp3"

    media_file = request.files.get(spec["file"])
    media_url_in = (request.form.get(spec["url"]) or "").strip()
    model_size = (request.form.get("whisper_model") or "").strip()
    if (
        model_size != "sensevoice"
        and model_size not in whisper_transcribe.ALLOWED_MODEL_SIZES
    ):
        model_size = whisper_transcribe.DEFAULT_MODEL_SIZE

    engine_error = _engine_error(model_size, language)
    if engine_error:
        return jsonify({"error": engine_error}), 400

    temppath = current_app.env_config.temppath
    os.makedirs(temppath, exist_ok=True)

    audio_temp_path = None
    media_url = None
    source_uri = None
    title = (request.form.get(spec["title"]) or "").strip()

    if media_file and media_file.filename:
        fname = (media_file.filename or "").lower()
        ext = os.path.splitext(fname)[1].lstrip(".")
        if ext not in spec["extensions"]:
            return jsonify({"error": spec["extension_error"]}), 400
        task_ref = uuid.uuid4().hex
        audio_temp_path = os.path.join(temppath, f"whisper_{task_ref}.{ext}")
        media_file.save(audio_temp_path)
        source_uri = media_file.filename
        if not title:
            base = media_file.filename or spec["fallback_title"]
            title = ".".join(base.split(".")[:-1]) or base
    elif media_url_in:
        # Transcription needs a local file, so the media is downloaded
        # regardless of size (capped); files beyond the local-storage
        # cutoff stream from the URL once the book exists.
        try:
            size = _url_content_length(media_url_in)
            fname = download_url_to_file(
                media_url_in,
                temppath,
                max_bytes=whisper_transcribe.WHISPER_MAX_DOWNLOAD_BYTES,
            )
        except BookImportException as e:
            return jsonify({"error": e.message}), 400
        audio_temp_path = os.path.join(temppath, fname)
        if size is not None and size > MEDIA_LOCAL_MAX_BYTES:
            media_url = media_url_in
        source_uri = media_url_in
        if not title:
            base = os.path.basename(urllib.parse.urlparse(media_url_in).path)
            title = base or spec["fallback_title"]
    else:
        return (
            jsonify(
                {
                    "error": f"Please provide a {spec['source_label']} file "
                    "(upload or an online URL)."
                }
            ),
            400,
        )

    task_id = whisper_transcribe.start_task(
        current_app._get_current_object(),  # pylint: disable=protected-access
        audio_temp_path,
        language,
        model_size,
        {
            "language_id": int(language_id),
            "title": title[:200],
            "tags": _parse_tagify_tags(request.form.get(spec["tag"], "")),
            "source_uri": source_uri,
            "book_type": book_type,
        },
        media_url=media_url,
        username=get_current_user(),
    )
    return jsonify({"task_id": task_id})


@bp.route("/retranscribe/<int:bookid>", methods=["POST"])
def whisper_retranscribe(bookid):
    """
    Re-run transcription on an existing mp3 / video book's media,
    replacing the book's text and subtitle cues in place (edit page
    button).

    The book's own stored media file is never touched; only a
    remote-URL book downloads a disposable temp copy for the
    transcription.  The page polls /whisper/status/<task_id>; the task
    updates the book (same flow as saving the edit form) instead of
    creating a new one.
    """
    if whisper_transcribe.has_running_task():
        return (
            jsonify(
                {
                    "error": "A transcription or model download is already "
                    "running -- please wait for it to finish."
                }
            ),
            409,
        )
    whisper_transcribe.purge_finished_tasks()

    dbbook = _find_book(bookid)
    if dbbook is None:
        return jsonify({"error": "Book not found."}), 404
    if (dbbook.book_type or "") not in RETRANSCRIBE_BOOK_TYPES:
        return (
            jsonify({"error": "Only mp3 and video books can be re-transcribed."}),
            400,
        )

    language = db.session.get(Language, dbbook.language_id)
    if language is None:
        return jsonify({"error": "The book's language is missing."}), 400

    model_size = (request.form.get("whisper_model") or "").strip()
    if model_size not in whisper_transcribe.ALLOWED_MODEL_SIZES + ["sensevoice"]:
        model_size = whisper_transcribe.DEFAULT_MODEL_SIZE
    engine_error = _engine_error(model_size, language)
    if engine_error:
        return jsonify({"error": engine_error}), 400

    temppath = current_app.env_config.temppath
    os.makedirs(temppath, exist_ok=True)

    temp_audio = False
    if dbbook.audio_filename:
        audio_path = os.path.join(
            current_app.env_config.useraudiopath, dbbook.audio_filename
        )
        if not os.path.exists(audio_path):
            return (
                jsonify({"error": "The book's media file is missing on disk."}),
                400,
            )
    elif dbbook.media_url:
        # Transcription needs a local file: stream a disposable copy.
        try:
            fname = download_url_to_file(
                dbbook.media_url,
                temppath,
                max_bytes=whisper_transcribe.WHISPER_MAX_DOWNLOAD_BYTES,
            )
        except BookImportException as e:
            return jsonify({"error": e.message}), 400
        audio_path = os.path.join(temppath, fname)
        temp_audio = True
    else:
        return jsonify({"error": "This book has no media to transcribe."}), 400

    task_id = whisper_transcribe.start_task(
        current_app._get_current_object(),  # pylint: disable=protected-access
        audio_path,
        language,
        model_size,
        None,  # book_params: unused, the book already exists
        username=get_current_user(),
        retranscribe_book_id=bookid,
        temp_audio=temp_audio,
    )
    return jsonify({"task_id": task_id})


@bp.route("/status/<task_id>", methods=["GET"])
def whisper_task_status(task_id):
    "Poller payload for a transcription task."
    return jsonify(whisper_transcribe.task_status(task_id))


@bp.route("/download_model", methods=["POST"])
@admin_only_if_multiuser_json
def whisper_download_model():
    """
    Pre-download the selected model so the first transcription starts
    fast.  Runs on the same background machinery and mutex as
    transcriptions; poll /whisper/status/<task_id> for completion.
    """
    if whisper_transcribe.has_running_task():
        return (
            jsonify(
                {
                    "error": "A transcription or model download is already "
                    "running -- please wait for it to finish."
                }
            ),
            409,
        )
    whisper_transcribe.purge_finished_tasks()

    model_size = (request.form.get("whisper_model") or "").strip()
    if model_size == "sensevoice":
        # The SenseVoice model files are independent of faster-whisper.
        task_id = whisper_transcribe.start_model_download(
            current_app._get_current_object(),  # pylint: disable=protected-access
            model_size,
        )
        return jsonify({"task_id": task_id})
    if not whisper_transcribe.whisper_status()["installed"]:
        return jsonify({"error": "faster-whisper is not installed yet."}), 400
    if model_size not in whisper_transcribe.ALLOWED_MODEL_SIZES:
        return (
            jsonify(
                {
                    "error": "Unknown model size.  Pick one of: "
                    + ", ".join(whisper_transcribe.ALLOWED_MODEL_SIZES + ["sensevoice"])
                }
            ),
            400,
        )

    task_id = whisper_transcribe.start_model_download(
        current_app._get_current_object(),  # pylint: disable=protected-access
        model_size,
    )
    return jsonify({"task_id": task_id})


@bp.route("/models", methods=["GET"])
def whisper_models():
    "Install state + per-size model cache status, for the Settings page."
    return jsonify(
        {
            "installed": whisper_transcribe.whisper_status()["installed"],
            "models": whisper_transcribe.model_cache_info(),
            "sensevoice": _sensevoice_status(),
        }
    )


def _sensevoice_status():
    "SenseVoice engine state for the Settings page; None when unknown."
    try:
        from lute.book import sensevoice

        return sensevoice.status()
    except Exception:  # pylint: disable=broad-except
        return None


@bp.route("/delete_model", methods=["POST"])
@admin_only_if_multiuser_json
def whisper_delete_model():
    "Remove a downloaded model from the server's cache."
    if whisper_transcribe.has_running_task():
        return (
            jsonify(
                {
                    "error": "A transcription or model download is already "
                    "running -- please wait for it to finish."
                }
            ),
            409,
        )
    ok, message = whisper_transcribe.delete_model(
        request.form.get("whisper_model") or ""
    )
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)
