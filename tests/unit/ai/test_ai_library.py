"""
Import/export of AI explanations: parsing, matching, overwrite, report.

The format is the same structured JSON the model returns, wrapped in one
book-level object (or one JSON object per line).  Import never calls a
provider and never enqueues in the rate limiter.
"""

import io
import json

import pytest

from lute.ai import library, service
from lute.ai.limiter import LIMITER
from lute.db import db
from lute.models.ai_result import AiResult
from lute.models.book import Sentence
from tests.utils import make_book

SENTENCES = [
    "Hello there, friend.",
    "A much longer sentence that stays similar.",
    "Goodbye.",
]


def _make_book(language, sentences=SENTENCES):
    "A book whose sentence rows are written directly, so text is stable."
    book = make_book("Library book", "placeholder", language)
    db.session.add(book)
    db.session.commit()
    text = book.texts[0]
    for index, sentence in enumerate(sentences, start=1):
        db.session.add(
            Sentence(tx_id=text.id, order=index, text_content=sentence)
        )
    db.session.commit()
    return book


def _wrapper(book, lang_id, items, target_lang="zh", model="mix"):
    payload = {
        "version": 1,
        "book_id": book.id,
        "lang_id": lang_id,
        "target_lang": target_lang,
        "model": model,
        "items": items,
    }
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _import(stream, filename="explanations.json", **kwargs):
    return library.import_file(stream, filename, **kwargs)


def _item(sentence, translation, **extra):
    payload = {"sentence": sentence, "translation": translation}
    payload.update(extra)
    return payload


def test_read_items_from_a_wrapper_array():
    payload = {"version": 1, "items": [_item("One.", "一"), _item("Two.", "二")]}
    stream = io.BytesIO(json.dumps(payload).encode("utf-8"))
    assert list(library.read_items(stream, "x.json")) == [
        _item("One.", "一"),
        _item("Two.", "二"),
    ]


def test_read_items_from_a_bare_array():
    stream = io.BytesIO(json.dumps([_item("One.", "一")]).encode("utf-8"))
    assert list(library.read_items(stream, "x.json")) == [_item("One.", "一")]


def test_read_items_from_jsonl():
    lines = json.dumps(_item("One.", "一")) + "\n\n" + json.dumps(_item("Two.", "二"))
    stream = io.BytesIO(lines.encode("utf-8"))
    assert list(library.read_items(stream, "x.jsonl")) == [
        _item("One.", "一"),
        _item("Two.", "二"),
    ]


def test_bad_jsonl_line_is_a_library_error():
    stream = io.BytesIO(b'{"sentence": "One.", "translation": "y"}\nnot json\n')
    with pytest.raises(library.LibraryError):
        list(library.read_items(stream, "x.jsonl"))


def test_match_sentence_levels():
    "exact, punctuation-only, fuzzy and no match."
    sentences = [library.normalize_sentence(s) for s in SENTENCES]

    assert library.match_sentence("Hello there, friend.", sentences) == (
        "Hello there, friend.",
        "exact",
    )
    assert library.match_sentence("Hello there friend", sentences) == (
        "Hello there, friend.",
        "punct",
    )
    fuzzy = "A much longer sentence that stays s1milar."
    matched, level = library.match_sentence(fuzzy, sentences)
    assert matched == "A much longer sentence that stays similar."
    assert level == "fuzzy"
    assert library.match_sentence("Completely unrelated words.", sentences) == (
        None,
        None,
    )


def test_import_matches_three_levels_and_reports_them(app_context, empty_db, english):
    book = _make_book(english)
    items = [
        _item("Hello there, friend.", "你好，朋友。"),
        _item("Goodbye", "再见"),
        _item("A much longer sentence that stays s1milar.", "相似句子"),
        _item("Completely unrelated words.", "无关"),
    ]
    report = _import(_wrapper(book, english.id, items))

    assert report["total"] == 4
    assert report["added"] == 3
    assert report["unmatched"] == 1
    assert report["levels"] == {"exact": 1, "punct": 1, "fuzzy": 1}
    assert report["unmatched_items"] == ["Completely unrelated words."]
    assert db.session.query(AiResult).count() == 3


def test_reimport_skips_then_overwrites(app_context, empty_db, english):
    book = _make_book(english)
    original = _wrapper(
        book, english.id, [_item("Hello there, friend.", "旧译文")]
    )
    report = _import(original)
    assert report["added"] == 1

    report = _import(
        _wrapper(book, english.id, [_item("Hello there, friend.", "旧译文")])
    )
    assert report["skipped"] == 1
    assert report["added"] == 0

    report = _import(
        _wrapper(book, english.id, [_item("Hello there, friend.", "新译文")]),
        overwrite=True,
    )
    assert report["overwritten"] == 1
    assert db.session.query(AiResult).count() == 1
    row = db.session.query(AiResult).one()
    assert row.source == "import"
    assert json.loads(row.result_json)["translation"] == "新译文"

    # A live reader with its own provider/model still gets the import.
    meta = {}
    data = service.sentence_cached_result(
        "Hello there, friend.", "gpt-4o-mini", "English", "zh", "live-entry",
        meta=meta,
    )
    assert data["translation"] == "新译文"
    assert meta["saved_from"] == "import"


def test_overwrite_replaces_a_live_row(app_context, empty_db, english):
    "An imported overwrite must win over the earlier live result."
    book = _make_book(english)
    assert service.save_result(
        service.RESULT_TYPE_SENTENCE,
        "Hello there, friend.",
        {"translation": "live text", "segments": []},
        provider_id="live-entry",
        model="gpt-4o-mini",
        target_lang="zh",
        lang_id=english.id,
        source="live",
    )
    report = _import(
        _wrapper(book, english.id, [_item("Hello there, friend.", "imported")]),
        overwrite=True,
    )
    assert report["overwritten"] == 1
    data = service.sentence_cached_result(
        "Hello there, friend.", "gpt-4o-mini", "English", "zh", "live-entry"
    )
    assert data["translation"] == "imported"


def test_invalid_items_are_reported_and_the_rest_import(
    app_context, empty_db, english
):
    book = _make_book(english)
    items = [
        _item("Hello there, friend.", "好", kind="poetry"),
        _item("A much longer sentence that stays similar.", 42),
        _item("Goodbye.", "再见", segments="not a list"),
        _item("Goodbye.", "再见"),
    ]
    report = _import(_wrapper(book, english.id, items))
    assert report["total"] == 4
    assert len(report["errors"]) == 3
    assert report["skipped"] == 0
    assert report["added"] == 1
    assert "unknown kind" in report["errors"][0]["reason"]


def test_context_that_is_only_a_material_label_is_refused(
    app_context, empty_db, english
):
    "A bare '课文叙述' context must not be imported as a usable result."
    book = _make_book(english)
    items = [
        _item("Hello there, friend.", "你好，朋友。", context="课文叙述"),
    ]
    report = _import(_wrapper(book, english.id, items))
    assert report["added"] == 0
    assert len(report["errors"]) == 1
    assert "material-type label" in report["errors"][0]["reason"]


def test_grammar_items_keep_their_kind(app_context, empty_db, english):
    book = _make_book(english)
    items = [
        {
            "kind": "grammar",
            "sentence": "Hello there, friend.",
            "translation": "你好，朋友。",
            "skeleton": {"subject": "you"},
            "grammar_points": [{"name": "greeting"}],
        }
    ]
    report = _import(_wrapper(book, english.id, items))
    assert report["added"] == 1
    row = db.session.query(AiResult).one()
    assert row.result_type == "grammar"
    data = service.grammar_cached_result(
        "Hello there, friend.", "m", "English", "zh", "p"
    )
    assert data["translation"] == "你好，朋友。"


def test_a_foreign_or_missing_book_is_refused(app_context, empty_db, english):
    stream = io.BytesIO(
        json.dumps(
            {
                "book_id": 99999,
                "lang_id": english.id,
                "target_lang": "zh",
                "items": [],
            }
        ).encode("utf-8")
    )
    with pytest.raises(library.LibraryError):
        _import(stream)


def test_export_round_trips(app_context, empty_db, english):
    book = _make_book(english)
    items = [_item("Hello there, friend.", "你好，朋友。")]
    _import(_wrapper(book, english.id, items))

    exported = library.export_book(book.id)
    assert exported["version"] == 1
    assert exported["book_id"] == book.id
    assert exported["model"] == "mixed"
    assert exported["items"][0]["sentence"] == "Hello there, friend."
    assert exported["items"][0]["translation"] == "你好，朋友。"

    stream = io.BytesIO(json.dumps(exported, ensure_ascii=False).encode("utf-8"))
    report = _import(stream, overwrite=True)
    assert report["added"] == 0
    assert report["overwritten"] == 1


def test_import_never_touches_the_rate_limiter(
    app_context, empty_db, english, monkeypatch
):
    book = _make_book(english)
    monkeypatch.setattr(
        "lute.ai.service.translate_batch",
        lambda *a, **k: pytest.fail("import must not call a provider"),
    )
    before = LIMITER.snapshot("import-entry", 20)
    _import(
        _wrapper(book, english.id, [_item("Hello there, friend.", "好")])
    )
    after = LIMITER.snapshot("import-entry", 20)
    assert before["queued"] == after["queued"] == 0
    assert before["tokens"] == after["tokens"]


# --- Offline export must match what an online reader sees -----------------


def test_export_online_lookup_replaces_the_books_sentence_row(
    app_context, empty_db, english
):
    "The panel resolves by sentence, not by book: export must do the same."
    book = _make_book(english)
    _import(_wrapper(book, english.id, [_item(SENTENCES[0], "书里的译文")]))

    def lookup(sentence, lang_id):
        assert sentence == SENTENCES[0]
        assert lang_id == english.id
        return {"translation": "联网看到的译文", "segments": [{"part": "Hello"}]}

    exported = library.export_book(book.id, online_lookup=lookup)
    assert exported["match"] == "online"
    item = exported["items"][0]
    assert item["translation"] == "联网看到的译文"
    assert item["segments"] == [{"part": "Hello"}]
    assert "书里的译文" not in json.dumps(exported, ensure_ascii=False)


def test_export_online_lookup_miss_keeps_the_book_row(
    app_context, empty_db, english
):
    "A sentence with no online hit still exports the book's own answer."
    book = _make_book(english)
    _import(_wrapper(book, english.id, [_item(SENTENCES[0], "书里的译文")]))
    exported = library.export_book(book.id, online_lookup=lambda s, l: None)
    assert exported["items"][0]["translation"] == "书里的译文"


def test_export_default_stays_per_book(app_context, empty_db, english):
    "The web UI's export is unchanged unless ?match=online is asked for."
    book = _make_book(english)
    _import(_wrapper(book, english.id, [_item(SENTENCES[0], "你好")]))
    exported = library.export_book(book.id)
    assert exported["match"] == "book"
    assert exported["items"][0]["translation"] == "你好"
