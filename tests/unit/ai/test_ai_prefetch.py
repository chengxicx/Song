"""
Background prefetch: queueing, superseding, cancellation, silent failure.
"""

import pytest

from lute.ai import prefetch, service
from lute.ai.limiter import LIMITER


@pytest.fixture(autouse=True)
def _no_worker(monkeypatch):
    "No real thread: the tests inspect the queue directly."
    prefetch._queue.clear()  # pylint: disable=protected-access
    prefetch._active.clear()  # pylint: disable=protected-access
    monkeypatch.setattr(prefetch, "_ensure_worker", lambda app: None)
    monkeypatch.setattr(service, "prefetch_enabled", lambda: True)
    yield
    prefetch._queue.clear()  # pylint: disable=protected-access
    prefetch._active.clear()  # pylint: disable=protected-access


def _job(**kwargs):
    "A job shaped like the ones schedule() builds."
    payload = {
        "key": ("u", 1, 2),
        "token": object(),
        "book_id": 1,
        "lang_id": 2,
        "sentences": ["A.", "B."],
        "source_lang": "English",
        "target_lang": "Chinese",
    }
    payload.update(kwargs)
    return payload


def test_disabled_switch_queues_nothing(monkeypatch):
    monkeypatch.setattr(service, "prefetch_enabled", lambda: False)
    assert prefetch.schedule("u", 1, 2, ["A.", "B."], "English", "Chinese") == 0
    assert prefetch.pending_count() == 0


def test_only_the_first_four_sentences_are_queued(
    monkeypatch, app_context
):  # pylint: disable=unused-argument
    monkeypatch.setattr(service, "prefetch_count", lambda: 4)
    sentences = ["One.", "Two.", "Three.", "Four.", "Five.", "Six."]
    assert prefetch.schedule("u", 1, 2, sentences, "English", "Chinese") == 4
    assert prefetch.pending_count() == 1
    job = prefetch._queue[0]  # pylint: disable=protected-access
    assert job["sentences"] == ["One.", "Two.", "Three.", "Four."]


def test_a_new_page_replaces_the_queued_job(app_context):  # pylint: disable=unused-argument
    prefetch.schedule("u", 1, 2, ["Old."], "English", "Chinese")
    prefetch.schedule("u", 1, 2, ["New."], "English", "Chinese")
    assert prefetch.pending_count() == 1
    assert prefetch._queue[0]["sentences"] == ["New."]  # pylint: disable=protected-access


def test_another_book_keeps_its_own_job(app_context):  # pylint: disable=unused-argument
    prefetch.schedule("u", 1, 2, ["Book one."], "English", "Chinese")
    prefetch.schedule("u", 2, 3, ["Book two."], "English", "Chinese")
    assert prefetch.pending_count() == 2


def test_cancel_drops_the_queue_and_the_active_token(app_context):  # pylint: disable=unused-argument
    prefetch.schedule("u", 1, 2, ["A."], "English", "Chinese")
    job = prefetch._queue[0]  # pylint: disable=protected-access
    prefetch.cancel("u", 1)
    assert prefetch.pending_count() == 0
    assert prefetch._is_current(job) is False


def test_run_one_stores_results_with_the_prefetch_source(monkeypatch):
    stored = []
    monkeypatch.setattr(prefetch, "_is_current", lambda job: True)
    monkeypatch.setattr(
        service,
        "sentence_provider_for_language_id",
        lambda lang_id: "p1",
    )
    monkeypatch.setattr(
        service,
        "grammar_ready",
        lambda provider: {"provider": "p1", "model": "m1", "rpm": 20},
    )
    monkeypatch.setattr(service, "sentence_cached_result", lambda *a, **k: None)
    monkeypatch.setattr(LIMITER, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(
        service,
        "translate_batch",
        lambda provider, sentences, source, target, usage=None: (
            [],
            [("A.", {"translation": "甲"})],
        ),
    )
    monkeypatch.setattr(
        service,
        "store_sentence_result",
        lambda *a, **k: stored.append(k),
    )

    prefetch._run_one(_job())  # pylint: disable=protected-access
    assert stored[0]["source"] == "prefetch"
    assert stored[0]["book_id"] == 1
    assert stored[0]["lang_id"] == 2
    assert stored[0]["provider_id"] == "p1"


def test_run_one_does_not_replace_a_row_stored_during_the_batch(monkeypatch):
    "A live Regenerate that finishes mid-batch must win."
    stored = []
    calls = {"n": 0}

    def cached(*_args, **_kwargs):
        calls["n"] += 1
        # The first pass (pending check) sees no row for A. or B.; the
        # second pass (just before storing) sees the live result.
        return None if calls["n"] <= 2 else {"translation": "Fresh."}

    monkeypatch.setattr(prefetch, "_is_current", lambda job: True)
    monkeypatch.setattr(
        service,
        "sentence_provider_for_language_id",
        lambda lang_id: "p1",
    )
    monkeypatch.setattr(
        service,
        "grammar_ready",
        lambda provider: {"provider": "p1", "model": "m1", "rpm": 20},
    )
    monkeypatch.setattr(service, "sentence_cached_result", cached)
    monkeypatch.setattr(LIMITER, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(
        service,
        "translate_batch",
        lambda provider, sentences, source, target, usage=None: (
            [],
            [("A.", {"translation": "甲"}), ("B.", {"translation": "乙"})],
        ),
    )
    monkeypatch.setattr(
        service,
        "store_sentence_result",
        lambda *a, **k: stored.append(k),
    )

    prefetch._run_one(_job())  # pylint: disable=protected-access
    assert stored == []
    assert calls["n"] == 4


def test_run_one_returns_silently_on_provider_failure(monkeypatch):
    monkeypatch.setattr(prefetch, "_is_current", lambda job: True)
    monkeypatch.setattr(
        service, "sentence_provider_for_language_id", lambda lang_id: "p1"
    )
    monkeypatch.setattr(
        service,
        "grammar_ready",
        lambda provider: {"provider": "p1", "model": "m1", "rpm": 20},
    )
    monkeypatch.setattr(service, "sentence_cached_result", lambda *a, **k: None)
    monkeypatch.setattr(LIMITER, "acquire", lambda *a, **k: True)

    def boom(*args, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(service, "translate_batch", boom)
    prefetch._run_one(_job())  # pylint: disable=protected-access
