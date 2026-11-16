"""
HTTP client for the Eudic (欧路词典) studylist OpenAPI.

Two behaviours here are not optional niceties:

* **Throttling.**  The API allows 30 requests per minute and 500 per 30
  minutes, and exceeding it gets the key suspended for 1 or 24 hours.
  A full pull is 51 pages, so an unthrottled client is a self-inflicted
  outage.  The limiter is process-wide (class-level) because the budget
  belongs to the key, not to one user's request.

* **Error classification.**  A 401 means "stop and tell the user", a 429
  means "stop now", while a 5xx may already have saved part of a batch.
  The caller decides what to do from the exception type, so the types
  have to be right.

The API key is only ever placed in the request header.  It is never
logged, never included in an exception message, and never returned by
this module.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import requests


class EudicError(Exception):
    "Base class for every Eudic failure."

    def __init__(self, message, kind="api", status=None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status

    def __str__(self):
        return self.message


class EudicAuthError(EudicError):
    "The API key was rejected (401)."

    def __init__(self, message="Eudic rejected the API key."):
        super().__init__(message, kind="auth", status=401)


class EudicRateLimitError(EudicError):
    "Too many requests, or the key is suspended (403/429)."

    def __init__(self, message="Eudic is rate limiting this key.", status=429):
        super().__init__(message, kind="rate_limit", status=status)


class EudicNotConfigured(EudicError):
    "No API key stored yet."

    def __init__(self, message="Eudic API key is not configured."):
        super().__init__(message, kind="not_configured")


@dataclass(frozen=True)
class RemoteWord:
    "One word as Eudic reports it."

    word: str
    star: int = 1
    add_time: str = ""
    phon: str = ""
    exp: str = ""
    context_line: str = ""

    @classmethod
    def from_json(cls, d):
        "Build from an API object, tolerating missing fields."
        return cls(
            word=str(d.get("word") or ""),
            star=int(d.get("star") or 0),
            add_time=str(d.get("add_time") or ""),
            phon=str(d.get("phon") or ""),
            exp=str(d.get("exp") or ""),
            context_line=str(d.get("context_line") or ""),
        )


@dataclass(frozen=True)
class RemoteCategory:
    "One studylist (生词本) inside a bucket."

    id: str
    bucket: str
    name: str


@dataclass
class RemoteSnapshot:
    "Every word in one studylist, keyed for matching."

    bucket: str
    category_id: str
    words: Dict[str, RemoteWord] = field(default_factory=dict)
    duplicates: List[RemoteWord] = field(default_factory=list)
    pages_fetched: int = 0
    truncated: bool = False

    @property
    def total(self):
        "Number of distinct words seen."
        return len(self.words) + len(self.duplicates)


class EudicClient:
    """
    Thin, synchronous wrapper over the studylist endpoints.

    ``sleep`` and ``clock`` are injectable so the tests can assert the
    throttle without spending two seconds per request.
    """

    BASE_URL = "https://api.frdic.com/api/open/v1/studylist"
    MIN_INTERVAL_SECONDS = 2.1
    PAGE_SIZE = 100
    MAX_PAGES = 51
    WINDOW_SECONDS = 30 * 60
    WINDOW_MAX_REQUESTS = 450
    RETRY_DELAY_SECONDS = 5.0

    _THROTTLE_LOCK = threading.Lock()
    _LAST_REQUEST_AT = 0.0
    _RECENT = deque()

    def __init__(
        self,
        api_key,
        session=None,
        timeout=(10, 60),
        min_interval=None,
        sleep=None,
        clock=None,
    ):
        self._api_key = (api_key or "").strip()
        self._session = session or requests.Session()
        self._timeout = timeout
        self._min_interval = (
            self.MIN_INTERVAL_SECONDS if min_interval is None else min_interval
        )
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    # -- throttle ---------------------------------------------------------

    @classmethod
    def reset_throttle(cls):
        "Clear the process-wide rate state (tests only)."
        with cls._THROTTLE_LOCK:
            cls._LAST_REQUEST_AT = 0.0
            cls._RECENT.clear()

    def _throttle(self):
        """
        Wait until another request is allowed, or raise if the 30-minute
        window is exhausted.  Leaves a 10% margin on the window.
        """
        cls = type(self)
        with cls._THROTTLE_LOCK:
            now = self._clock()
            while cls._RECENT and now - cls._RECENT[0] > self.WINDOW_SECONDS:
                cls._RECENT.popleft()
            if len(cls._RECENT) >= self.WINDOW_MAX_REQUESTS:
                raise EudicRateLimitError(
                    "Eudic request budget for 30 minutes is exhausted; "
                    "wait before syncing again.",
                    status=None,
                )
            wait = self._min_interval - (now - cls._LAST_REQUEST_AT)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            cls._LAST_REQUEST_AT = now
            cls._RECENT.append(now)

    # -- transport --------------------------------------------------------

    def _headers(self):
        return {
            "Authorization": f"NIS {self._api_key}",
            "Accept": "application/json",
        }

    def _request(self, method, path, params=None, json_body=None, allow_retry=True):
        """
        Perform one API call and return the decoded body ({} if empty).

        401/403/400 are never retried: they will fail the same way and
        each retry spends a slot of the 30/minute budget.
        """
        if not self._api_key:
            raise EudicNotConfigured()

        url = f"{self.BASE_URL}{path}"
        self._throttle()
        try:
            resp = self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.Timeout as e:
            if allow_retry:
                self._sleep(self.RETRY_DELAY_SECONDS)
                return self._request(method, path, params, json_body, allow_retry=False)
            raise EudicError("Eudic timed out.", kind="timeout") from e
        except requests.RequestException as e:
            if allow_retry:
                self._sleep(self.RETRY_DELAY_SECONDS)
                return self._request(method, path, params, json_body, allow_retry=False)
            raise EudicError("Could not reach Eudic.", kind="network") from e

        status = resp.status_code
        if status in (200, 201, 204):
            if status == 204 or not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as e:
                raise EudicError(
                    "Eudic returned a non-JSON response.", kind="bad_response"
                ) from e

        message = self._message_from(resp)
        if status == 400:
            raise EudicError(message or "Eudic rejected the request.", status=400)
        if status == 401:
            raise EudicAuthError(message or "Eudic rejected the API key.")
        if status in (403, 429):
            raise EudicRateLimitError(
                message or "Eudic is rate limiting this key.", status=status
            )
        if status == 404:
            raise EudicError(message or "Not found.", kind="not_found", status=404)
        if status >= 500:
            if allow_retry:
                self._sleep(self.RETRY_DELAY_SECONDS)
                return self._request(method, path, params, json_body, allow_retry=False)
            raise EudicError(
                message or "Eudic could not save the data.",
                kind="server",
                status=status,
            )
        raise EudicError(message or f"Unexpected response {status}.", status=status)

    @staticmethod
    def _message_from(resp):
        "Pull the API's own message out of a failed response."
        try:
            payload = resp.json()
        except ValueError:
            return ""
        if isinstance(payload, dict):
            return str(payload.get("message") or "")
        return ""

    # -- studylists -------------------------------------------------------

    def list_categories(self, bucket):
        "Every studylist in a bucket."
        payload = self._request("GET", "/category", params={"language": bucket})
        rows = payload.get("data") or []
        return [
            RemoteCategory(
                id=str(r.get("id")),
                bucket=str(r.get("language") or bucket),
                name=str(r.get("name") or ""),
            )
            for r in rows
        ]

    def create_category(self, bucket, name):
        "Create a studylist; returns its id."
        payload = self._request(
            "POST", "/category", json_body={"language": bucket, "name": name}
        )
        data = payload.get("data") or {}
        return str(data.get("id") or "")

    def fetch_words_page(self, bucket, category_id, page, page_size=None, sort="asc"):
        "One page of a studylist."
        params = {
            "language": bucket,
            "category_id": str(category_id),
            "page": int(page),
            "page_size": int(page_size or self.PAGE_SIZE),
        }
        if sort:
            params["sort"] = sort
        try:
            payload = self._request("GET", "/words", params=params)
        except EudicError as e:
            # Older deployments reject sort=; the ordering still holds
            # by add time, so drop the parameter rather than fail.
            if e.status == 400 and sort:
                return self.fetch_words_page(
                    bucket, category_id, page, page_size, sort=None
                )
            raise
        rows = payload.get("data") or []
        return [RemoteWord.from_json(r) for r in rows]

    def fetch_snapshot(self, bucket, category_id, on_page=None):
        """
        Read a whole studylist into a snapshot keyed by ``word_key``.

        The API caps paging at page 50 with 100 per page, so a list with
        more than ~5100 words cannot be read in full.  That is reported
        via ``truncated`` instead of being silently dropped.
        """
        from lute.eudic.mapping import word_key

        snapshot = RemoteSnapshot(bucket=bucket, category_id=str(category_id))
        page = 0
        for page in range(self.MAX_PAGES):
            rows = self.fetch_words_page(bucket, category_id, page)
            for rw in rows:
                key = word_key(rw.word)
                if not key:
                    continue
                if key in snapshot.words:
                    # Eudic dedupes case-insensitively on insert, so two
                    # entries for one key means historical junk; keep the
                    # first (oldest, since we sort ascending) and report.
                    snapshot.duplicates.append(rw)
                    continue
                snapshot.words[key] = rw
            snapshot.pages_fetched = page + 1
            if on_page is not None:
                on_page(page + 1, len(rows))
            if len(rows) < self.PAGE_SIZE:
                break
        else:
            snapshot.truncated = True
        return snapshot

    # -- words ------------------------------------------------------------

    def add_words(self, bucket, category_id, items: Sequence[Tuple[str, int]]):
        """
        Bulk upsert ``[(word, star), ...]`` (max 1000 per call).

        Eudic ignores everything but the first occurrence of a word, and
        this call cannot carry a translation or an example sentence.
        """
        payload = {
            "language": bucket,
            "category_id": str(category_id),
            "items": [{"word": w, "star": int(s)} for w, s in items],
        }
        body = self._request("POST", "/words", json_body=payload)
        return str(body.get("message") or "")

    def add_word(
        self, bucket, category_id, word, star, context_line="", category_ids=None
    ):
        "Add one word, optionally with its example sentence."
        payload = {
            "language": bucket,
            "word": word,
            "star": int(star),
            "category_ids": [str(c) for c in (category_ids or [category_id])],
        }
        if context_line:
            payload["context_line"] = context_line
        body = self._request("POST", "/word", json_body=payload)
        return str(body.get("message") or "")

    def get_word(self, bucket, word):
        "Look one word up; None when Eudic does not have it."
        try:
            payload = self._request(
                "GET", "/word", params={"language": bucket, "word": word}
            )
        except EudicError as e:
            if e.status == 404:
                return None
            raise
        if not isinstance(payload, dict) or not payload.get("word"):
            return None
        return RemoteWord.from_json(payload)

    def delete_words(self, bucket, category_id, words):
        "Remove words from a studylist."
        self._request(
            "DELETE",
            "/words",
            json_body={
                "language": bucket,
                "category_id": str(category_id),
                "words": list(words),
            },
        )
