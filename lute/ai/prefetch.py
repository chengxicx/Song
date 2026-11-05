"""
Background prefetch of the next sentences on a reading page.

When a page opens, the first few sentences are queued here and analysed
in one batched request, so clicking one of them opens a stored result
instead of paying for a round trip.  The work is strictly best-effort:

* prefetch tickets sit in the rate limiter's prefetch queue, so a live
  sentence or grammar request always goes first;
* a failure is silent and simply leaves the sentence uncached (clicking
  it later computes it normally);
* only the current book and language are prefetched -- opening another
  page or book supersedes the queued job;
* the Settings switch (``ai_prefetch_enabled``) turns the whole thing off.

One daemon worker serialises the jobs: a page open per keystroke must not
spawn a request per page.
"""

import threading
from collections import deque

from flask import current_app

from lute.ai import service
from lute.ai.limiter import LIMITER, PREFETCH
from lute.multiuser import context as mu_context

_lock = threading.Condition()
_queue = deque()
_active = {}  # (username, book_id, lang_id) -> token of the newest job
_worker_started = False
_worker_app = None


def _ensure_worker(app):
    "Start the single worker thread on first use."
    global _worker_started, _worker_app
    if _worker_started:
        return
    _worker_app = app
    thread = threading.Thread(
        target=_worker_loop,
        name="lute-ai-prefetch",
        daemon=True,
    )
    thread.start()
    _worker_started = True


def _next_job():
    with _lock:
        while not _queue:
            _lock.wait()
        return _queue.popleft()


def _is_current(job):
    "False once a newer page/book/language superseded this job."
    key = job["key"]
    with _lock:
        return _active.get(key) is job["token"]


def _cancel_key(key):
    "Forget queued jobs for one key; the running one checks _is_current."
    with _lock:
        kept = deque(job for job in _queue if job["key"] != key)
        _queue.clear()
        _queue.extend(kept)


def schedule(username, book_id, lang_id, sentences, source_lang, target_lang):
    """
    Queue the current page's first sentences for background analysis.

    Returns the number of sentences queued (0 when disabled, unconfigured,
    or nothing to do).  Safe to call on every page open.
    """
    if not service.prefetch_enabled():
        return 0
    key = (username or "", book_id, lang_id)
    texts = [service.clean_sentence(s) for s in sentences or []]
    texts = [t for t in texts if t][: service.prefetch_count()]
    if not texts:
        return 0
    token = object()
    job = {
        "key": key,
        "token": token,
        "book_id": book_id,
        "lang_id": lang_id,
        "sentences": texts,
        "source_lang": source_lang,
        "target_lang": target_lang,
    }
    with _lock:
        # A new page supersedes whatever was queued for the old one.
        kept = deque(item for item in _queue if item["key"] != key)
        _queue.clear()
        _queue.extend(kept)
        _active[key] = token
        _queue.append(job)
        _ensure_worker(current_app._get_current_object())
        _lock.notify()
    return len(texts)


def cancel(username, book_id, lang_id=None):
    "Drop queued prefetch work for a book (or one language of it)."
    with _lock:
        keys = [
            key for key in _active
            if key[0] == (username or "") and key[1] == book_id
            and (lang_id is None or key[2] == lang_id)
        ]
        for key in keys:
            _active[key] = object()
        kept = deque(item for item in _queue if item["key"] not in keys)
        _queue.clear()
        _queue.extend(kept)


def _worker_loop():
    while True:
        job = _next_job()
        try:
            with _worker_app.app_context(), mu_context.user_scope(job["key"][0]):
                _run_one(job)
        except Exception:  # noqa: BLE001 - a prefetch must never leak
            pass


def _run_one(job):
    if not _is_current(job):
        return
    if not service.prefetch_enabled():
        return
    try:
        ready = service.grammar_ready(service.sentence_provider_for_language_id(
            job["lang_id"]
        ))
    except service.AiError:
        return
    provider_id = ready["provider"]
    model = ready["model"]
    target_lang = job["target_lang"]
    pending = []
    for sentence in job["sentences"]:
        if not _is_current(job):
            return
        if service.sentence_cached_result(
            sentence, model, job["source_lang"], target_lang, provider_id
        ) is None:
            pending.append(sentence)
    if not pending:
        return
    # One request for the whole batch, behind every live request.
    if not LIMITER.acquire(
        provider_id, ready["rpm"], priority=PREFETCH, cancel=lambda: not _is_current(job)
    ):
        return
    usage = {}
    try:
        _, results = service.translate_batch(
            provider_id,
            pending,
            job["source_lang"],
            target_lang,
            usage=usage,
        )
    except Exception:  # noqa: BLE001 - silent: the next click retries
        return
    if not _is_current(job):
        return
    for sentence, parsed in results:
        service.store_sentence_result(
            sentence,
            model,
            job["source_lang"],
            target_lang,
            parsed,
            provider_id=provider_id,
            book_id=job["book_id"],
            lang_id=job["lang_id"],
            source="prefetch",
            token_usage=usage.get("usage"),
        )


def pending_count():
    "Observability for tests: jobs currently waiting."
    with _lock:
        return len(_queue)
