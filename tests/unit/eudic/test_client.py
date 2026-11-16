"""Tests for the Eudic HTTP client: throttling, paging, error mapping."""

import pytest

from lute.eudic.client import (
    EudicAuthError,
    EudicClient,
    EudicError,
    EudicNotConfigured,
    EudicRateLimitError,
)


class FakeResponse:
    "Just enough of requests.Response."

    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.content = b"" if payload is None else b"x"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    "Records calls and replays queued responses."

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params or {},
                "json": json,
                "headers": headers or {},
            }
        )
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


class FakeClock:
    "A clock that only moves when something sleeps."

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_throttle():
    EudicClient.reset_throttle()
    yield
    EudicClient.reset_throttle()


def build(responses, **kwargs):
    clock = kwargs.pop("clock", None) or FakeClock()
    session = FakeSession(responses)
    client = EudicClient(
        "SECRET-KEY",
        session=session,
        clock=clock.clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return client, session, clock


# --- auth header ----------------------------------------------------------


def test_authorization_header_is_nis_space_key():
    "NIS must be followed by exactly one space, or the API 401s."
    client, session, _ = build([FakeResponse(200, {"data": []})])
    client.list_categories("en")
    assert session.calls[0]["headers"]["Authorization"] == "NIS SECRET-KEY"


def test_missing_key_never_reaches_the_network():
    session = FakeSession([])
    client = EudicClient("", session=session)
    with pytest.raises(EudicNotConfigured):
        client.list_categories("en")
    assert session.calls == []


def test_the_key_never_appears_in_an_error_message():
    client, _, _ = build([FakeResponse(401, {"message": "授权认证未通过"})])
    with pytest.raises(EudicAuthError) as excinfo:
        client.list_categories("en")
    assert "SECRET-KEY" not in str(excinfo.value)


# --- throttling -----------------------------------------------------------


def test_requests_are_spaced_by_the_minimum_interval():
    client, _, clock = build([FakeResponse(200, {"data": []})] * 2)
    client.list_categories("en")
    client.list_categories("en")
    assert clock.slept == [pytest.approx(2.1)]


def test_the_thirty_minute_window_caps_the_request_count():
    client, _, _ = build([FakeResponse(200, {"data": []})])
    type(client).WINDOW_MAX_REQUESTS = 1
    try:
        client.list_categories("en")
        with pytest.raises(EudicRateLimitError):
            client.list_categories("en")
    finally:
        type(client).WINDOW_MAX_REQUESTS = 450


# --- paging ---------------------------------------------------------------


def _word(word, star=1):
    return {"word": word, "star": star, "add_time": "", "phon": "", "exp": ""}


def test_snapshot_stops_at_a_short_page():
    client, _, _ = build([FakeResponse(200, {"data": [_word("a"), _word("b")]})])
    snap = client.fetch_snapshot("en", "0")
    assert set(snap.words) == {"a", "b"}
    assert snap.pages_fetched == 1
    assert snap.truncated is False


def test_snapshot_flags_truncation_when_all_pages_are_full():
    "A list over ~5100 words cannot be read in full; say so, don't hide it."
    full = [_word(f"w{i}") for i in range(EudicClient.PAGE_SIZE)]
    client, session, _ = build(
        [FakeResponse(200, {"data": full}) for _ in range(EudicClient.MAX_PAGES)]
    )
    snap = client.fetch_snapshot("en", "0")
    assert snap.truncated is True
    assert snap.pages_fetched == EudicClient.MAX_PAGES
    assert len(session.calls) == EudicClient.MAX_PAGES


def test_snapshot_collapses_duplicate_keys_and_reports_them():
    "俺 and 俺 [おれ] are one word; keep the first, count the rest."
    client, _, _ = build(
        [
            FakeResponse(
                200,
                {"data": [_word("俺 [おれ]"), _word("俺"), _word("まつる")]},
            )
        ]
    )
    snap = client.fetch_snapshot("en", "134317089127793466")
    assert set(snap.words) == {"俺", "まつる"}
    assert snap.words["俺"].word == "俺 [おれ]"
    assert [d.word for d in snap.duplicates] == ["俺"]


def test_snapshot_skips_blank_words():
    client, _, _ = build([FakeResponse(200, {"data": [_word("  "), _word("ok")]})])
    snap = client.fetch_snapshot("en", "0")
    assert set(snap.words) == {"ok"}


def test_sort_parameter_is_dropped_on_a_400():
    "Older deployments reject sort=; the default order is already oldest-first."
    client, session, _ = build(
        [
            FakeResponse(400, {"message": "参数不正确：sort"}),
            FakeResponse(200, {"data": [_word("a")]}),
        ]
    )
    rows = client.fetch_words_page("en", "0", 0)
    assert [r.word for r in rows] == ["a"]
    assert "sort" not in session.calls[1]["params"]


# --- error mapping --------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, EudicAuthError),
        (403, EudicRateLimitError),
        (429, EudicRateLimitError),
    ],
)
def test_auth_and_rate_limit_statuses_map_to_their_types(status, expected):
    client, _, _ = build([FakeResponse(status, {"message": "nope"})], min_interval=0)
    with pytest.raises(expected):
        client.list_categories("en")


def test_bad_request_raises_a_plain_error_with_the_api_message():
    client, _, _ = build([FakeResponse(400, {"message": "参数不正确：language"})])
    with pytest.raises(EudicError) as excinfo:
        client.list_categories("ja")
    assert excinfo.value.status == 400
    assert "language" in str(excinfo.value)


def test_server_error_is_retried_once_then_raises():
    client, session, clock = build(
        [
            FakeResponse(500, {"message": "save failed"}),
            FakeResponse(500, {"message": "save failed"}),
        ],
        min_interval=0,
    )
    with pytest.raises(EudicError) as excinfo:
        client.add_words("en", "0", [("a", 1)])
    assert excinfo.value.kind == "server"
    assert len(session.calls) == 2


def test_get_word_returns_none_on_404():
    client, _, _ = build([FakeResponse(404, {"message": "单词不存在"})])
    assert client.get_word("en", "俺") is None


# --- payload shape --------------------------------------------------------


def test_bulk_add_sends_items_and_no_translation():
    """
    The bulk endpoint only accepts word+star; a translation cannot ride
    along, which is why the UI promises status sync only.
    """
    client, session, _ = build([FakeResponse(201, {"message": "ok"})])
    client.add_words("en", "0", [("apple", 3)])
    body = session.calls[0]["json"]
    assert body == {
        "language": "en",
        "category_id": "0",
        "items": [{"word": "apple", "star": 3}],
    }


def test_single_add_carries_the_context_line():
    client, session, _ = build([FakeResponse(201, {"message": "ok"})])
    client.add_word("en", "5", "apple", 3, context_line="An apple a day.")
    body = session.calls[0]["json"]
    assert body["context_line"] == "An apple a day."
    assert body["category_ids"] == ["5"]
