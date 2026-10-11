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

Every converted file lives in one folder inside the static folder -- a
*pool* of dictionaries, since a server can serve more than one language:

    <static>/dict/ja_zh_shogakukan_v2.db
    /static/dict/ja_zh_shogakukan_v2.db

The name ``ja_zh_shogakukan_v2.db`` is special only in that it is the
file the phone's default download URL points at, so a phone pointed at
this server picks that one up with no configuration.  Any other
``*.db`` in the folder is usable too; a language uses any of them via
'localdict' rows in its dictionary list (``LanguageDictionary`` with
``dicttype == "localdict"``, the dicturi holding the file name), and a
language with no such rows has no local dictionary.

Every function here takes the file name as its last argument, and
``None`` means "the default one" -- the phone's file.  The name always
comes from a language row or a form field, so it is validated before it
is joined onto the folder (see ``_is_pool_name``).

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

# The default dictionary's file name -- the one the phone app's
# dictionary URL uses (DictRepository.urlOnServer in LuteForMobile), so
# having it in the pool means the phone works with no setup at all.
# Other dictionaries may sit beside it; this one is what a caller gets
# when it does not name one.  A dictionary's real identity (title,
# entry count, where it came from) is in its metadata sidecar.
DB_FILENAME = "ja_zh_shogakukan_v2.db"

DICT_SUBDIR = "dict"

# A dictionary's file name: a plain "<something>.db" that can only ever
# name a file inside the pool.  The name is stored per language and
# comes back through a form field, so it is checked before being joined
# onto the folder -- no separators, no "..", no hidden files.
_POOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.db$")

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


def is_pool_name(name):
    """
    True when ``name`` may name a file in the dictionary pool.

    The value round-trips through a language row and a form field, so it
    is untrusted: a name with a separator or a ".." would let a caller
    read or delete anything the server user can.
    """
    return (
        bool(name) and isinstance(name, str) and _POOL_NAME_RE.match(name) is not None
    )


def resolve_name(filename):
    """
    The pool file name a caller means, or None when it is not a usable
    name.  ``None`` means the default dictionary -- the phone's file --
    but "" does *not*: an empty name is the value a language row holds
    when it has no dictionary, and falling back to the default there is
    exactly the bug the per-language setting exists to fix.
    """
    if filename is None:
        return DB_FILENAME
    return filename if is_pool_name(filename) else None


def suggested_filename(source_name):
    """
    A pool file name derived from the name of an uploaded .mdx.

    A file name may carry things a pool name may not -- folders, spaces,
    non-ASCII -- so they collapse to "-": the derived default is always
    usable, instead of failing a validation the user never asked for.
    """
    stem = os.path.splitext(os.path.basename(source_name or ""))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return f"{stem or 'dictionary'}.db"


def db_path(filename=None):
    "Absolute path of one pool dictionary, or None for an unusable name."
    name = resolve_name(filename)
    return os.path.join(dict_dir(), name) if name else None


def metadata_path(filename=None):
    "Absolute path of one pool dictionary's metadata sidecar, or None."
    path = db_path(filename)
    return f"{path}.json" if path else None


def download_url(filename=None):
    "The URL the phone (and any browser) can fetch the dictionary from."
    name = resolve_name(filename)
    if name is None:
        return None
    try:
        return url_for("static", filename=f"{DICT_SUBDIR}/{name}")
    except RuntimeError:
        # No request context (a CLI or a background task): the static
        # route's own path is fixed, so this is the same answer.
        return f"/static/{DICT_SUBDIR}/{name}"


def is_installed(filename=None):
    "True when that dictionary has been imported."
    path = db_path(filename)
    return path is not None and os.path.isfile(path)


def _read_metadata(filename=None):
    "The sidecar written at import time, or an empty dict."
    path = metadata_path(filename)
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def status(filename=None):
    """
    What the settings page and the language form show about one
    dictionary: whether it is installed, and what is in it.  Never
    raises -- a half-copied file reads as "not installed" rather than
    breaking the page.
    """
    name = resolve_name(filename)
    info = {
        "installed": False,
        "filename": name if name is not None else filename,
        "url": download_url(filename),
        "is_default": name == DB_FILENAME,
        "size_mb": None,
        "modified": None,
        "source": None,
        "title": None,
        "entries": None,
        "terms": None,
        "imported_at": None,
    }
    path = db_path(filename)
    if path is None:
        return info
    try:
        stat = os.stat(path)
    except OSError:
        return info

    info["installed"] = True
    info["size_mb"] = round(stat.st_size / (1024 * 1024), 1)
    info["modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    meta = _read_metadata(filename)
    for key in ("source", "title", "entries", "terms", "imported_at"):
        info[key] = meta.get(key)
    return info


def list_installed():
    """
    Every dictionary in the pool, the phone's default file first and the
    rest alphabetically.

    This is what the language form's dropdown is built from, so a
    dictionary that was dropped into the folder by hand (the CLI path in
    tools/mdx_to_sqlite.py) shows up with no extra bookkeeping.
    """
    try:
        names = [n for n in os.listdir(dict_dir()) if n.endswith(".db")]
    except OSError:
        return []
    names = [n for n in names if is_pool_name(n)]
    names.sort(key=lambda n: (n != DB_FILENAME, n.lower()))
    return [status(n) for n in names]


def remove(filename=None):
    """
    Delete one dictionary and its metadata.  Returns True when something
    was removed.
    """
    removed = False
    for path in (db_path(filename), metadata_path(filename)):
        if path is None:
            continue
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


def lookup(term, reading=None, filename=None):
    """
    The definition for ``term`` in one pool dictionary, or None.  Never
    raises: a missing, unusable or damaged dictionary simply means no
    gloss.

    ``filename`` is the language's ``local_dict``.  A caller that has a
    language in hand must pass it -- falling back to the default
    dictionary is how the *old* single-dictionary code ended up serving
    the Japanese dictionary under a Korean book.
    """
    candidates = lookup_candidates(term, reading)
    if not candidates:
        return None
    path = db_path(filename)
    if path is None or not os.path.isfile(path):
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


def _convert(mdx_path, source_name, filename=None, progress=None):
    """
    Convert ``mdx_path`` into one pool dictionary and record what is in
    it.  Returns the metadata dict.
    """
    out_path = db_path(filename)
    metadata = importer.convert(mdx_path, out_path, progress=progress)
    metadata["source"] = source_name
    metadata["imported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(metadata_path(filename), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return metadata


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _run_import(app, task_id, mdx_path, source_name, filename=None):
    "The background thread body."
    with app.app_context():
        try:
            _set_task(task_id, "running", processed=0)
            metadata = _convert(
                mdx_path,
                source_name,
                filename,
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


def start_import(app, mdx_path, source_name, filename=None):
    """
    Convert the MDX at ``mdx_path`` in the background into the pool
    dictionary ``filename`` (the default one when None), and return the
    task_id.  The caller owns ``mdx_path``: the task deletes it when it
    finishes, successfully or not.

    An existing dictionary of the same name is left alone until the
    conversion succeeds (importer.convert builds beside the target and
    renames), so re-importing never leaves a language with no
    dictionary to read.
    """
    if resolve_name(filename) is None:
        raise ValueError(f"Not a dictionary file name: {filename!r}")
    purge_finished_tasks()
    task_id = uuid.uuid4().hex
    _set_task(task_id, "queued", processed=0)
    thread = threading.Thread(
        target=_run_import,
        args=(app, task_id, mdx_path, source_name, filename),
        daemon=True,
    )
    thread.start()
    return task_id
