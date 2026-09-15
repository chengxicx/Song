"""
Tests for the optional Bilibili egress proxy.

The production server cannot reach Bilibili at all (its API answers HTTP
412 to the server's IP and no header or cookie helps), so the only way to
keep the DASH relay working there is outbound via an IP Bilibili accepts
-- see lute/utils/outbound_proxy.py.  These pin the contract: nothing
changes unless the variable is explicitly set.
"""

from unittest.mock import Mock, patch

from lute.utils.outbound_proxy import (
    BILIBILI_PROXY_ENV,
    bilibili_proxies,
    describe_bilibili_proxy,
)


def test_returns_none_when_unset(monkeypatch):
    "Unset means a direct call, exactly as before the feature existed."
    monkeypatch.delenv(BILIBILI_PROXY_ENV, raising=False)
    assert bilibili_proxies() is None


def test_returns_none_when_blank(monkeypatch):
    "A blank value must not be turned into an unusable proxy config."
    for blank in ("", "   ", "\t\n"):
        monkeypatch.setenv(BILIBILI_PROXY_ENV, blank)
        assert bilibili_proxies() is None


def test_returns_proxies_dict_when_set(monkeypatch):
    monkeypatch.setenv(BILIBILI_PROXY_ENV, "http://127.0.0.1:18888")
    assert bilibili_proxies() == {
        "http": "http://127.0.0.1:18888",
        "https": "http://127.0.0.1:18888",
    }


def test_surrounding_whitespace_is_trimmed(monkeypatch):
    monkeypatch.setenv(BILIBILI_PROXY_ENV, "  http://127.0.0.1:18888  ")
    assert bilibili_proxies()["https"] == "http://127.0.0.1:18888"


def test_describe_reports_both_states(monkeypatch):
    monkeypatch.delenv(BILIBILI_PROXY_ENV, raising=False)
    assert "not set" in describe_bilibili_proxy()
    monkeypatch.setenv(BILIBILI_PROXY_ENV, "http://127.0.0.1:18888")
    assert "http://127.0.0.1:18888" in describe_bilibili_proxy()


def test_api_call_passes_the_proxy_through(monkeypatch):
    "The configured proxy actually reaches requests.get."
    monkeypatch.setenv(BILIBILI_PROXY_ENV, "http://127.0.0.1:18888")
    from lute.read import bilibili_stream

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": 0, "data": {"pages": []}}

    with patch.object(bilibili_stream.requests, "get", return_value=response) as get:
        bilibili_stream._fetch_view("BV1xx411c7mD")  # pylint: disable=protected-access

    assert get.call_args.kwargs["proxies"] == {
        "http": "http://127.0.0.1:18888",
        "https": "http://127.0.0.1:18888",
    }


def test_api_call_passes_none_when_unset(monkeypatch):
    "With no proxy configured the kwarg is still present, but None."
    monkeypatch.delenv(BILIBILI_PROXY_ENV, raising=False)
    from lute.read import bilibili_stream

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"code": 0, "data": {"pages": []}}

    with patch.object(bilibili_stream.requests, "get", return_value=response) as get:
        bilibili_stream._fetch_view("BV1xx411c7mD")  # pylint: disable=protected-access

    assert get.call_args.kwargs["proxies"] is None


def test_cdn_segment_call_passes_the_proxy_through(monkeypatch):
    "The stream relay uses the proxy too -- the CDN blocks the server as well."
    monkeypatch.setenv(BILIBILI_PROXY_ENV, "http://127.0.0.1:18888")
    from lute.read import bilibili_stream

    response = Mock()
    response.status_code = 206
    response.headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 0-1/9"}
    response.iter_content.return_value = iter([b"ab"])

    with patch.object(bilibili_stream.requests, "get", return_value=response) as get:
        bilibili_stream.proxy_stream("https://cdn.example/x.m4s", "bytes=0-1")

    assert get.call_args.kwargs["proxies"] == {
        "http": "http://127.0.0.1:18888",
        "https": "http://127.0.0.1:18888",
    }
