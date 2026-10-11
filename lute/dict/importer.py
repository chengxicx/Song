"""
MDX -> SQLite conversion.

Produces the two-table dictionary both readers query (see
lute/dict/service.py for the schema and how it is served).  Kept free of
Flask so the CLI tool (tools/mdx_to_sqlite.py) and the web import share
exactly one implementation -- the phone app downloads whatever this
writes, so the two paths must not drift.
"""

import os
import sqlite3
import uuid

from lute.dict.mdx import MdxReader

# Definitions are capped on import, like the phone's own importer does: a
# card shows a few lines, and dictionaries carry kilobytes of markup per
# entry.
MAX_DEFINITION_CHARS = 800

# Entries between progress reports.
PROGRESS_EVERY = 2000

# Rows per commit.
_BATCH_SIZE = 4000


def convert(  # pylint: disable=too-many-locals,too-many-statements
    mdx_path, out_db_path, progress=None
):
    """
    Convert the MDX at ``mdx_path`` into a dictionary at ``out_db_path``.

    The file is built beside the target and renamed into place at the
    end, so a failed or interrupted run never replaces a working
    dictionary with a half-written one -- which is what makes the web
    import safe to re-run while the old dictionary is still being served.

    ``progress`` is called with the number of entries read so far.
    Returns {title, entries, terms, bytes}; raises on anything it cannot
    read, including an MDX that yields no usable entries.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_db_path)), exist_ok=True)
    tmp = f"{out_db_path}.{uuid.uuid4().hex}.tmp"
    try:
        with MdxReader.from_file(mdx_path) as reader:
            title = reader.title
            conn = sqlite3.connect(tmp)
            try:
                conn.execute("PRAGMA journal_mode=OFF")
                conn.execute("PRAGMA synchronous=OFF")
                conn.execute("CREATE TABLE entries(eid INTEGER PRIMARY KEY, def TEXT)")
                conn.execute(
                    "CREATE TABLE terms(word TEXT NOT NULL, eid INTEGER NOT NULL)"
                )
                conn.execute("CREATE INDEX idx_terms ON terms(word)")

                # Definitions repeat across headwords (variant spellings,
                # inflected keys); store each one once and point the terms
                # at it.
                eid_by_def = {}
                seen_words = set()
                entry_rows = []
                term_rows = []
                processed = 0
                terms = 0

                def flush():
                    if entry_rows:
                        conn.executemany("INSERT INTO entries VALUES(?,?)", entry_rows)
                        conn.executemany("INSERT INTO terms VALUES(?,?)", term_rows)
                        entry_rows.clear()
                        term_rows.clear()

                for raw_key, raw_definition in reader.entries():
                    processed += 1
                    if progress is not None and processed % PROGRESS_EVERY == 0:
                        progress(processed)
                    word = MdxReader.clean_key(raw_key)
                    if not word or word in seen_words:
                        continue
                    seen_words.add(word)
                    text = MdxReader.clean_definition(
                        raw_definition, max_chars=MAX_DEFINITION_CHARS
                    )
                    if not text:
                        continue
                    eid = eid_by_def.get(text)
                    if eid is None:
                        eid = len(eid_by_def) + 1
                        eid_by_def[text] = eid
                        entry_rows.append((eid, text))
                    term_rows.append((word, eid))
                    terms += 1
                    if len(term_rows) >= _BATCH_SIZE:
                        flush()
                flush()
                conn.commit()
                entries = len(eid_by_def)
            finally:
                conn.close()

        if entries == 0:
            raise ValueError(
                "No usable entries found -- is this really an MDX dictionary?"
            )

        metadata = {
            "title": title,
            "entries": entries,
            "terms": terms,
            "bytes": os.path.getsize(tmp),
        }
        os.replace(tmp, out_db_path)
        return metadata
    finally:
        # A successful run renamed the temp file away; anything still
        # here is a failure.
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
