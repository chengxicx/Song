"""
Background Eudic sync tasks.

A full pull is up to 51 requests at 2.1s apart (~107s) and a two-way run
is longer, which overruns the proxy timeout if done inside the POST.
So the POST starts a daemon thread and the page polls a status endpoint,
following the in-process task pattern used by the book import
(lute/book/import_task.py).

The table lives in this process only: a restart makes in-flight tasks
report ``unknown`` and the page asks the user to retry.  That is safe
because a sync is a reconciliation -- re-running it is a no-op.

One task per (user, language) at a time; a second click returns the
running task id instead of stacking another 51 requests behind it.
"""

import logging
import threading
import time
import uuid

from lute.db import db
from lute.eudic.service import EudicService
from lute.multiuser import context as mu_context

logger = logging.getLogger(__name__)

_TASKS = {}
_ACTIVE = {}
_TASKS_LOCK = threading.Lock()
TASK_TTL_SECONDS = 10 * 60


def _purge_locked(now, force=False):
    "Drop terminal tasks older than the TTL.  Caller holds the lock."
    expired = [
        tid
        for tid, t in _TASKS.items()
        if t.get("finished_at") is not None
        and (force or now - t["finished_at"] > TASK_TTL_SECONDS)
    ]
    for tid in expired:
        task = _TASKS.pop(tid, None)
        if task is not None:
            _ACTIVE.pop(task.get("key"), None)


def _set_state(task_id, state, percent=None, message=None, result=None, error=None):
    "Update one task's public fields."
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None:
            return
        task["state"] = state
        if percent is not None:
            task["percent"] = percent
        if message is not None:
            task["message"] = message
        if result is not None:
            task["result"] = result
        if error is not None:
            task["error"] = error
        if state in ("finished", "error"):
            task["finished_at"] = time.time()


def start(app, mode, scope, username=None):
    """
    Begin a sync; returns ``(task_id, duplicate)``.

    ``duplicate`` is True when this user already has a task running for
    the same language, in which case the existing id is returned.
    """
    key = (username, scope.lg_id, scope.bucket, scope.category_id)
    with _TASKS_LOCK:
        _purge_locked(time.time())
        existing = _ACTIVE.get(key)
        if existing is not None:
            task = _TASKS.get(existing)
            if task is not None and task["state"] in ("queued", "running"):
                return existing, True
        task_id = uuid.uuid4().hex
        _TASKS[task_id] = {
            "state": "queued",
            "percent": 0,
            "message": "Queued.",
            "result": None,
            "error": None,
            "username": username,
            "key": key,
            "mode": mode,
            "finished_at": None,
        }
        _ACTIVE[key] = task_id

    thread = threading.Thread(
        target=_run, args=(app, task_id, mode, scope, username), daemon=True
    )
    thread.start()
    return task_id, False


def task_status(task_id, username=None):
    "Poller payload; another user's task reads as unknown."
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if task is None or task.get("username") != username:
            return {"state": "unknown"}
        return {
            "state": task["state"],
            "percent": task.get("percent", 0),
            "message": task.get("message"),
            "result": task.get("result"),
            "error": task.get("error"),
        }


def purge_finished_tasks():
    "Drop every task; used by tests."
    with _TASKS_LOCK:
        _purge_locked(time.time(), force=True)


def _run(app, task_id, mode, scope, username):
    "Thread body: sync, then publish the result."
    try:
        with app.app_context(), mu_context.user_scope(username):
            try:
                _set_state(task_id, "running", percent=1, message="Starting")

                def progress(percent, message):
                    _set_state(task_id, "running", percent=percent, message=message)

                service = EudicService(db.session)
                result = service.sync(scope, mode=mode, progress=progress)
                payload = result.to_dict()
            finally:
                db.session.remove()
            _set_state(
                task_id,
                "finished",
                percent=100,
                message=payload.get("message") or "Done.",
                result=payload,
            )
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("eudic sync task %s failed", task_id)
        _set_state(task_id, "error", error=str(e))
