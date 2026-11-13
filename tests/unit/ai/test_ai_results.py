"""
Persistent result store: key dimensions, read fallbacks, migration.
"""

import json
from datetime import datetime, timedelta

from lute.ai import service
from lute.db import db
from lute.models.ai_result import AiResult
from lute.models.repositories import UserSettingRepository

SENTENCE = {"translation": "Hello.", "segments": [], "examples": []}


def _store(sentence="Hello.", result=None, **kwargs):
    return service.save_result(
        service.RESULT_TYPE_SENTENCE,
        sentence,
        result or SENTENCE,
        **kwargs,
    )


def test_provider_and_model_are_separate_keys(app_context, empty_db):
    assert _store(provider_id="p1", model="m1", target_lang="zh")
    assert _store(provider_id="p1", model="m2", target_lang="zh")
    assert _store(provider_id="p2", model="m1", target_lang="zh")
    assert _store(provider_id="p1", model="m1", target_lang="ja")
    assert db.session.query(AiResult).count() == 4


def test_any_model_fallback_finds_an_imported_row(app_context, empty_db):
    _store(
        result={"translation": "导入的译文", "segments": []},
        provider_id="import",
        model="any",
        target_lang="zh",
        source="import",
    )
    meta = {}
    data = service.sentence_cached_result(
        "Hello.", "live-model", "English", "zh", "live-entry", meta=meta
    )
    assert data["translation"] == "导入的译文"
    assert meta["saved_from"] == "import"


def test_the_newest_row_wins_among_models(app_context, empty_db):
    _store(
        result={"translation": "old", "segments": []},
        provider_id="import",
        model="any",
        target_lang="zh",
        source="import",
    )
    _store(
        result={"translation": "new", "segments": []},
        provider_id="live",
        model="other",
        target_lang="zh",
        source="live",
    )
    older = db.session.query(AiResult).filter_by(provider_id="import").one()
    newer = db.session.query(AiResult).filter_by(provider_id="live").one()
    older.updated_at = datetime.now() - timedelta(minutes=5)
    newer.updated_at = datetime.now()
    db.session.commit()

    meta = {}
    data = service.sentence_cached_result(
        "Hello.", "any-model", "English", "zh", "", meta=meta
    )
    assert data["translation"] == "new"
    assert meta["saved_from"] == "live"


def test_exact_row_beats_a_newer_row_of_another_model(app_context, empty_db):
    _store(
        result={"translation": "exact", "segments": []},
        provider_id="p1",
        model="m1",
        target_lang="zh",
    )
    _store(
        result={"translation": "other", "segments": []},
        provider_id="p2",
        model="m2",
        target_lang="zh",
    )
    data = service.sentence_cached_result("Hello.", "m1", "English", "zh", "p1")
    assert data["translation"] == "exact"


def test_legacy_ttl_row_is_upgraded_on_read(app_context, empty_db):
    legacy_key = service.sentence_cache_key("Hello.", "m1", "English", "zh")
    UserSettingRepository(db.session).set_dynamic_value(
        legacy_key, json.dumps(SENTENCE, ensure_ascii=False)
    )
    db.session.commit()

    meta = {}
    data = service.sentence_cached_result(
        "Hello.", "m1", "English", "zh", "p1", meta=meta
    )
    assert data["translation"] == "Hello."
    assert meta["saved_from"] == "legacy-ttl"
    assert db.session.query(AiResult).filter_by(sentence="Hello.").count() == 1


def test_migration_copies_old_entries_once(app_context, empty_db):
    legacy_key = service.sentence_cache_key("Hello.", "m1", "English", "zh")
    UserSettingRepository(db.session).set_dynamic_value(
        legacy_key, json.dumps(SENTENCE, ensure_ascii=False)
    )
    db.session.commit()

    assert service.ensure_results_migration() == 1
    row = db.session.query(AiResult).one()
    assert row.source == "legacy"
    assert row.key == legacy_key
    # The flag makes it a one-time job.
    assert service.ensure_results_migration() == 0
    assert db.session.query(AiResult).count() == 1


def test_delete_by_book_and_language(app_context, empty_db):
    _store(book_id=1, lang_id=10, target_lang="zh")
    _store(sentence="Other.", book_id=1, lang_id=11, target_lang="zh")
    _store(sentence="Third.", book_id=2, lang_id=10, target_lang="zh")
    assert service.delete_results(book_id=1, lang_id=10) == 1
    assert db.session.query(AiResult).count() == 2
    assert service.delete_results(lang_id=10) == 1
    assert db.session.query(AiResult).count() == 1
