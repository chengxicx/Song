"""
Local dictionary storage, import and lookup.

The reader shows a gloss for terms Lute has no translation for yet -- the
word you have never studied -- and the phone app downloads the same file
to work offline.  Both read one small two-table SQLite file that the
server builds once from an MDX (MDict) dictionary:

    entries(eid INTEGER PRIMARY KEY, def TEXT)
    terms(word TEXT NOT NULL, eid INTEGER NOT NULL)   + index on word

``def`` is the definition already flattened to plain text (see
MdxReader.clean_definition), so neither client has to strip HTML.

The file is published into the static folder, under the name the phone's
default download URL points at, so a phone pointed at this server picks
it up with no configuration:

    <static>/dict/ja_zh_shogakukan_v2.db
    /static/dict/ja_zh_shogakukan_v2.db

Lookups open the file read-only, per call: a few sub-milliseconds of
sqlite3 setup against a user-driven popup, in exchange for never holding
a shared connection across waitress' request threads.
"""

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
import uuid
from datetime import datetime

from flask import current_app, url_for

from lute.dict import importer

# Re-exported: the definition cap is the importer's, but it is part of
# what this module promises about the published dictionary.
from lute.dict.importer import MAX_DEFINITION_CHARS  # pylint: disable=unused-import

# The published file name.  It is the name the phone app's default
# dictionary URL uses (DictRepository.urlOnServer in LuteForMobile), so
# publishing under it means the phone works with no setup at all.  The
# installed dictionary's real identity is in the metadata sidecar.
DB_FILENAME = "ja_zh_shogakukan_v2.db"

DICT_SUBDIR = "dict"

# Leading honorifics are not part of a dictionary key: the key for
# おひさしぶり is ひさしぶり.
_HONORIFIC_RE = re.compile(r"^[おご]")

# Lute stores multi-token terms with a zero-width space between tokens;
# a dictionary key never has one.
_ZERO_WIDTH_SPACE = "\u200b"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def dict_dir():
    """
    The dictionary folder inside the static folder.

    Deliberately does not create it: this is on the lookup path, which
    runs for every term popup, and the folder only needs creating when a
    dictionary is written (importer.convert does it).
    """
    return os.path.join(current_app.static_folder, DICT_SUBDIR)


def db_path():
    "Absolute path of the published dictionary."
    return os.path.join(dict_dir(), DB_FILENAME)


def metadata_path():
    "Absolute path of the published dictionary's metadata sidecar."
    return os.path.join(dict_dir(), f"{DB_FILENAME}.json")


def download_url():
    "The URL the phone (and any browser) can fetch the dictionary from."
    try:
        return url_for("static", filename=f"{DICT_SUBDIR}/{DB_FILENAME}")
    except RuntimeError:
        # No request context (a CLI or a background task): the static
        # route's own path is fixed, so this is the same answer.
        return f"/static/{DICT_SUBDIR}/{DB_FILENAME}"


def is_installed():
    "True when a dictionary has been imported."
    return os.path.isfile(db_path())


def _read_metadata():
    "The sidecar written at import time, or an empty dict."
    try:
        with open(metadata_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def status():
    """
    What the settings page shows: whether a dictionary is installed, and
    what is in it.  Never raises -- a half-copied file reads as
    "not installed" rather than breaking the page.
    """
    path = db_path()
    info = {
        "installed": False,
        "filename": DB_FILENAME,
        "url": download_url(),
        "size_mb": None,
        "modified": None,
        "source": None,
        "title": None,
        "entries": None,
        "terms": None,
        "imported_at": None,
    }
    try:
        stat = os.stat(path)
    except OSError:
        return info

    info["installed"] = True
    info["size_mb"] = round(stat.st_size / (1024 * 1024), 1)
    info["modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    meta = _read_metadata()
    for key in ("source", "title", "entries", "terms", "imported_at"):
        info[key] = meta.get(key)
    return info


def remove():
    """
    Delete the published dictionary and its metadata.  Returns True when
    something was removed.
    """
    removed = False
    for path in (db_path(), metadata_path()):
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def lookup_candidates(term, reading):
    """
    The words to try, in order: the term itself, then its reading, then
    the reading with a leading honorific stripped.

    Most Japanese-Chinese dictionaries key on the kana reading (the
    dictionary's typical structure), so a term written in kanji is only
    found through its reading.  Inflected forms are not resolved yet --
    the phone's importer has the same gap.
    """
    words = []

    def add(word):
        word = (word or "").replace(_ZERO_WIDTH_SPACE, "").strip()
        if word and word not in words:
            words.append(word)

    add(term)
    if reading:
        add(reading)
        stripped = _HONORIFIC_RE.sub("", reading, count=1)
        if stripped != reading:
            add(stripped)
    return words


def _connect_read_only(path):
    "Read-only sqlite3 connection to the published dictionary."
    uri = f"file:{urllib.request.pathname2url(path)}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def lookup(term, reading=None):
    """
    The definition for ``term``, or None.  Never raises: a missing or
    damaged dictionary simply means no gloss.
    """
    candidates = lookup_candidates(term, reading)
    if not candidates:
        return None
    path = db_path()
    if not os.path.isfile(path):
        return None
    try:
        conn = _connect_read_only(path)
    except sqlite3.Error:
        return None
    try:
        for word in candidates:
            row = conn.execute(
                "SELECT entries.def FROM terms"
                " JOIN entries ON entries.eid = terms.eid"
                " WHERE terms.word = ? LIMIT 1",
                (word,),
            ).fetchone()
            if row is not None and row[0]:
                return row[0]
        return None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
#
# Conversion runs on a daemon thread instead of in the request: an MDX
# dictionary is 50-500 MB and the parse takes minutes, far beyond any
# reverse proxy's timeout.  The POST returns a task_id immediately and
# the settings page polls for progress -- the same shape as the
# shadowing scoring task (lute/read/shadowing.py).

_TASKS = {}
_TASKS_LOCK = threading.Lock()
_TASK_TTL_SECONDS = 10 * 60


def _set_task(task_id, state, result=None, error=None, processed=None):
    with _TASKS_LOCK:
        task = _TASKS.setdefault(task_id, {})
        task["state"] = state
        task["result"] = result
        task["error"] = error
        if processed is not None:
            task["processed"] = processed
        task["ts"] = time.time()


def _set_progress(task_id, processed):
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is not None:
            task["processed"] = processed
            task["ts"] = time.time()


def purge_finished_tasks():
    "Drop terminal tasks past the TTL so the registry stays small."
    cutoff = time.time() - _TASK_TTL_SECONDS
    with _TASKS_LOCK:
        stale = [
            tid
            for tid, t in _TASKS.items()
            if t["state"] in ("finished", "error") and t["ts"] < cutoff
        ]
        for tid in stale:
            del _TASKS[tid]


def task_status(task_id):
    "A snapshot of one task, or None if it is unknown or expired."
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return None
        return dict(task)


def _convert(mdx_path, source_name, progress=None):
    """
    Convert ``mdx_path`` into the published dictionary and record what is
    in it.  Returns the metadata dict.
    """
    metadata = importer.convert(mdx_path, db_path(), progress=progress)
    metadata["source"] = source_name
    metadata["imported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(metadata_path(), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return metadata


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _run_import(app, task_id, mdx_path, source_name):
    "The background thread body."
    with app.app_context():
        try:
            _set_task(task_id, "running", processed=0)
            metadata = _convert(
                mdx_path,
                source_name,
                progress=lambda n: _set_progress(task_id, n),
            )
        except Exception as e:  # pylint: disable=broad-except
            _remove_quietly(mdx_path)
            _set_task(task_id, "error", error=str(e))
            return
        # Drop the upload before announcing the result, so a caller that
        # sees a terminal state can rely on the temp file being gone.
        _remove_quietly(mdx_path)
        _set_task(task_id, "finished", result=metadata)


def start_import(app, mdx_path, source_name):
    """
    Convert the MDX at ``mdx_path`` in the background and return the
    task_id.  The caller owns ``mdx_path``: the task deletes it when it
    finishes, successfully or not.
    """
    purge_finished_tasks()
    task_id = uuid.uuid4().hex
    _set_task(task_id, "queued", processed=0)
    thread = threading.Thread(
        target=_run_import,
        args=(app, task_id, mdx_path, source_name),
        daemon=True,
    )
    thread.start()
    return task_id
