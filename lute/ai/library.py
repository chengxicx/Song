"""
Import and export of AI explanations ("AI explanations library").

The file format is deliberately the same structured JSON the model
already returns, so an outside AI can be handed an exported book and its
reply can be imported back:

    {"version": 1, "book_id": 123, "lang_id": 13, "target_lang": "zh",
     "model": "mixed", "items": [ ... ]}

``items`` may be a JSON array (one object per sentence) or the whole file
may be JSONL (one object per line).  Each item carries a ``kind``
(``sentence`` by default, or ``grammar``).

Importing never calls a provider and never touches the rate limiter:
it only writes rows to the persistent result table with
``provider_id=import`` and ``source=import``.
"""

import json
import re
from difflib import SequenceMatcher

from sqlalchemy import or_

from lute.ai import service
from lute.db import db
from lute.models.ai_result import AiResult
from lute.models.book import Book, Sentence, Text
from lute.models.language import Language

MAX_IMPORT_BYTES = 50 * 1024 * 1024
FUZZY_THRESHOLD = 0.90
_GRAMMAR_KEYS = (
    "skeleton",
    "clauses",
    "grammar_points",
    "tense_voice",
    "pitfalls",
)


class LibraryError(Exception):
    "A file-level import/export failure the UI should show."

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------
# Sentence matching
# ---------------------------------------------------------------------


def normalize_sentence(text):
    "Trim and collapse whitespace; also drop Lute's zero-width spaces."
    text = (text or "").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def _punctuationless(text):
    "Normalized text without punctuation, for the second match level."
    return re.sub(r"[^\w\s]", "", normalize_sentence(text))


def _similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def book_sentences(book_id):
    """
    Every sentence in the book, normalized and de-duplicated.

    Lute stores one row per sentence; the AI reader panel passes the
    same text with zero-width spaces removed, so normalizing here keeps
    the keys aligned.
    """
    rows = (
        db.session.query(Sentence.text_content)
        .join(Text, Sentence.tx_id == Text.id)
        .filter(Text.bk_id == int(book_id))
        .all()
    )
    seen = set()
    out = []
    for (text,) in rows:
        cleaned = normalize_sentence(text)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def match_sentence(item_text, sentences):
    """
    Find the book's sentence for an imported item.

    Returns ``(sentence, level)`` where level is ``exact``, ``punct``,
    ``fuzzy``, or ``None`` when nothing is close enough.  Exact and
    punctuation-only matches are checked first; the fuzzy pass only
    compares candidates of a similar length so a large book stays fast.
    """
    wanted = normalize_sentence(item_text)
    if not wanted:
        return None, None
    for sentence in sentences:
        if sentence == wanted:
            return sentence, "exact"
    bare = _punctuationless(wanted)
    for sentence in sentences:
        if _punctuationless(sentence) == bare:
            return sentence, "punct"
    best = None
    best_score = FUZZY_THRESHOLD
    low = len(wanted) * 0.7
    high = len(wanted) * 1.3 + 2
    for sentence in sentences:
        if not low <= len(sentence) <= high:
            continue
        score = _similarity(wanted, sentence)
        if score > best_score:
            best_score = score
            best = sentence
    if best is not None:
        return best, "fuzzy"
    return None, None


# ---------------------------------------------------------------------
# File parsing (JSON and JSONL, streamed)
# ---------------------------------------------------------------------


def _chars(stream, chunk_size=65536):
    "Yield characters without loading the whole upload into memory."
    # Decode with an incremental decoder: a multi-byte UTF-8 char can
    # straddle a chunk boundary, and decoding each chunk on its own
    # with errors="replace" would turn it into U+FFFD garbage.
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            return
        if isinstance(chunk, bytes):
            chunk = decoder.decode(chunk)
        for char in chunk:
            yield char


def _iter_array_objects(chars):
    "Yield raw JSON object strings from an array, one object at a time."
    depth = 0
    buffer = []
    in_string = False
    escaped = False
    for char in chars:
        if in_string:
            buffer.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            buffer.append(char)
        elif char == "{":
            depth += 1
            buffer.append(char)
        elif char == "}":
            depth -= 1
            buffer.append(char)
            if depth == 0 and buffer:
                yield "".join(buffer)
                buffer = []
        elif depth > 0:
            buffer.append(char)


def _iter_jsonl(stream):
    "One JSON object per non-empty line."
    for line in stream:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError as e:
            raise LibraryError(f"Invalid JSONL line: {e}") from e


def _iter_json_file(stream):
    """
    A wrapper object with an ``items`` array, or a bare array.

    The array is streamed object by object, so a large export can be
    imported without holding the whole document in memory.
    """
    chars = _chars(stream)
    first = ""
    for char in chars:
        if not char.isspace():
            first = char
            break
    if first == "[":
        for raw in _iter_array_objects(chars):
            yield json.loads(raw)
        return
    if first != "{":
        raise LibraryError("The file is not a JSON object or array.")
    # Skip the wrapper's own opening brace and scan for "items".
    tail = ""
    for char in chars:
        tail = (tail + char)[-9:]
        if tail.endswith('"items"'):
            break
    else:
        raise LibraryError('The JSON file has no "items" array.')
    for char in chars:
        if char == ":":
            break
    for char in chars:
        if char == "[":
            break
    else:
        raise LibraryError('The "items" value is not an array.')
    for raw in _iter_array_objects(chars):
        yield json.loads(raw)


def read_items(stream, filename=""):
    "Yield item dicts from either format, raising LibraryError on bad JSON."
    name = (filename or "").lower()
    try:
        if name.endswith(".jsonl") or name.endswith(".ndjson"):
            yield from _iter_jsonl(stream)
        else:
            yield from _iter_json_file(stream)
    except LibraryError:
        raise
    except ValueError as e:
        raise LibraryError(f"Invalid JSON: {e}") from e


def wrapper_defaults(stream):
    """
    A cheap best-effort pre-read of the wrapper fields (book_id, lang_id,
    target_lang, model).  Only the first 4 KB are inspected, and any
    failure simply returns empty defaults.
    """
    if not hasattr(stream, "seek"):
        return {}
    try:
        stream.seek(0)
        head = stream.read(4096)
        if isinstance(head, bytes):
            head = head.decode("utf-8", errors="replace")
        stream.seek(0)
    except Exception:  # pylint: disable=broad-exception-caught
        return {}
    out = {}
    for key in ("book_id", "lang_id"):
        match = re.search(rf'"{key}"\s*:\s*(\d+)', head)
        if match:
            out[key] = int(match.group(1))
    for key in ("target_lang", "model"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]*)"', head)
        if match:
            out[key] = match.group(1)
    return out


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _is_str_list(value):
    return isinstance(value, list) and all(isinstance(x, dict) for x in value)


def _validate_item(item):
    """
    Return (kind, cleaned result) or raise ValueError with a readable
    reason.  One bad item must never fail the whole file.
    """
    if not isinstance(item, dict):
        raise ValueError("item is not a JSON object")
    sentence = item.get("sentence")
    if not isinstance(sentence, str) or not normalize_sentence(sentence):
        raise ValueError("missing sentence")
    kind = (item.get("kind") or "sentence").strip().lower()
    if kind not in (service.RESULT_TYPE_SENTENCE, service.RESULT_TYPE_GRAMMAR):
        raise ValueError(f"unknown kind {kind!r}")
    translation = item.get("translation")
    if not isinstance(translation, str):
        raise ValueError("translation must be a string")
    if kind == service.RESULT_TYPE_SENTENCE:
        for key in ("segments", "grammar", "examples"):
            if key in item and not _is_str_list(item[key]):
                raise ValueError(f"{key} must be a list of objects")
        if "context" in item and not isinstance(item["context"], str):
            raise ValueError("context must be a string")
        result = {
            key: item[key]
            for key in service._SENTENCE_JSON_KEYS  # pylint: disable=protected-access
            if key in item
        }
        return kind, result
    if not any(key in item for key in _GRAMMAR_KEYS):
        raise ValueError("grammar item has no grammar fields")
    result = {
        key: item[key]
        for key in service._GRAMMAR_JSON_KEYS  # pylint: disable=protected-access
        if key in item
    }
    return kind, result


# ---------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------


def import_file(stream, filename, target_lang="", overwrite=False):
    """
    Import one file and return a report dict.

    Every item is validated on its own; a bad item is counted and listed
    but the rest of the file still imports.
    """
    defaults = wrapper_defaults(stream)
    target_lang = (target_lang or defaults.get("target_lang") or "").strip()
    model = (defaults.get("model") or "import").strip() or "import"
    book_id = defaults.get("book_id")
    lang_id = defaults.get("lang_id")

    book = db.session.get(Book, book_id) if book_id else None
    if book is None:
        raise LibraryError(
            "The file must declare a book_id from your own library."
        )
    lang = db.session.get(Language, lang_id) if lang_id else None
    if lang is None:
        raise LibraryError("The file must declare a valid lang_id.")
    if not target_lang:
        raise LibraryError(
            'The file must declare a "target_lang" (or pick one on the form).'
        )

    sentences = book_sentences(book.id)
    report = {
        "book_id": book.id,
        "book_title": book.title,
        "lang_id": lang.id,
        "target_lang": target_lang,
        "total": 0,
        "added": 0,
        "overwritten": 0,
        "skipped": 0,
        "unmatched": 0,
        "levels": {"exact": 0, "punct": 0, "fuzzy": 0},
        "errors": [],
        "unmatched_items": [],
    }

    for index, item in enumerate(read_items(stream, filename), start=1):
        report["total"] += 1
        try:
            kind, result = _validate_item(item)
        except ValueError as e:
            report["errors"].append({"line": index, "reason": str(e)})
            continue
        entry_sentence = item["sentence"]
        matched, level = match_sentence(entry_sentence, sentences)
        if matched is None:
            report["unmatched"] += 1
            # The UI shows the first ten; the CSV download carries all.
            report["unmatched_items"].append(entry_sentence)
            continue
        report["levels"][level] += 1
        rows = (
            db.session.query(AiResult)
            .filter(
                AiResult.result_type == kind,
                AiResult.sentence == matched,
                AiResult.target_lang == target_lang,
                or_(
                    AiResult.book_id == book.id, AiResult.book_id.is_(None)
                ),
            )
            .all()
        )
        if rows and not overwrite:
            report["skipped"] += 1
            continue
        if rows:
            report["overwritten"] += 1
            # Overwriting means the imported text wins even for a live
            # reader whose provider/model would otherwise hit the old
            # exact row first; the new import row is the newest one.
            for row in rows:
                db.session.delete(row)
            db.session.commit()
        else:
            report["added"] += 1
        ok = service.save_result(
            kind,
            matched,
            result,
            provider_id="import",
            model=model,
            target_lang=target_lang,
            book_id=book.id,
            lang_id=lang.id,
            source="import",
        )
        if not ok:
            report["errors"].append(
                {"line": index, "reason": "could not store the result"}
            )

    return report


# ---------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------


def export_book(book_id, lang_id=None, target_lang=None):
    """
    Every stored explanation for one book, in the importable format.

    Rows are exported one per item with their own target_lang and kind,
    so a re-import round-trips even when several languages are present.
    """
    book = db.session.get(Book, int(book_id))
    if book is None:
        raise LibraryError("Book not found.")
    query = db.session.query(AiResult).filter(AiResult.book_id == book.id)
    if lang_id is not None:
        query = query.filter(AiResult.lang_id == int(lang_id))
    if target_lang:
        query = query.filter(AiResult.target_lang == target_lang)
    rows = query.order_by(AiResult.id).all()

    items = []
    for row in rows:
        try:
            payload = json.loads(row.result_json or "")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        item = {"sentence": row.sentence, "kind": row.result_type}
        item.update(payload)
        if row.target_lang:
            item["target_lang"] = row.target_lang
        items.append(item)

    langs = sorted({item.get("target_lang", "") for item in items if item.get("target_lang")})
    return {
        "version": 1,
        "book_id": book.id,
        "book_title": book.title,
        "lang_id": int(lang_id) if lang_id is not None else book.language_id,
        "target_lang": target_lang or (langs[0] if len(langs) == 1 else "mixed"),
        "model": "mixed",
        "items": items,
    }
