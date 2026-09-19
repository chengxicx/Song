"""
JapaneseSudachiParser tests.

Besides the normal parse / reading / lemma behavior, these tests pin
down the concurrency contract: the parser is called from the reading
page *and* from the subtitle-words AJAX endpoint at the same time, and
sudachipy's Tokenizer is not thread-safe (concurrent tokenize() calls
raise "RuntimeError: Already borrowed"), so the parser has to hand each
thread its own tokenizer while sharing the dictionary.
"""

import threading

import pytest

pytest.importorskip("sudachipy")
pytest.importorskip("sudachidict_core")

from lute.models.language import Language
from lute.parse.sudachi_parser import JapaneseSudachiParser
from lute.settings.current import current_settings


def _make_parser():
    "A parser, or a skip if sudachipy / a sudachi dict isn't installed."
    if not JapaneseSudachiParser.is_supported():
        pytest.skip("sudachipy and a sudachi dictionary are required")
    return JapaneseSudachiParser()


@pytest.fixture(autouse=True)
def _fresh_sudachi_cache():
    "Every test starts with an empty tokenizer and dictionary cache."

    def clear():
        JapaneseSudachiParser._invalidate_cache()
        JapaneseSudachiParser._thread_local.tokenizer = None
        JapaneseSudachiParser._thread_local.tokenizer_key = None

    clear()
    yield
    clear()


def _japanese_language():
    "A language configured like the bundled Japanese definition."
    lang = Language()
    lang.regexp_split_sentences = ".!?。？！"
    return lang


def _tokens(text, language=None):
    "Parse text, returning [[token, is_word], ...]."
    p = JapaneseSudachiParser()
    language = language or _japanese_language()
    return [[t.token, t.is_word] for t in p.get_parsed_tokens(text, language)]


# ---- basic parsing ----


def test_parses_a_sentence(app_context):
    "A simple sentence is split into words plus an end-of-paragraph token."
    toks = _tokens("私は元気です。")
    words = [t[0] for t in toks if t[0] != "¶"]
    assert words == ["私", "は", "元気", "です", "。"], words
    assert toks[-1] == ["¶", False]


def test_symbols_are_not_selectable(app_context):
    "Punctuation parses as a non-word so it isn't clickable on the page."
    toks = dict((t[0], t[1]) for t in _tokens("元気です。"))
    assert toks["元気"] is True
    assert toks["です"] is True
    assert toks["。"] is False


def test_multiline_text_gets_a_sentinel_per_paragraph(app_context):
    "Each paragraph is closed with its own ¶ token."
    toks = _tokens("元気です。\n元気ですか。")
    assert [t[0] for t in toks].count("¶") == 2


def test_reading_and_lemma(app_context):
    "Readings and dictionary forms come back for inflected words."
    current_settings()["japanese_reading"] = "hiragana"
    p = _make_parser()
    assert p.get_reading("強い") == "つよい"
    assert p.get_lemma("広がっ") == "広がる"


# ---- threading ----


def test_tokenizer_is_reused_within_a_thread(app_context):
    "Repeated calls on one thread must not rebuild the tokenizer."
    _make_parser()
    first = JapaneseSudachiParser._build_tokenizer("core")
    second = JapaneseSudachiParser._build_tokenizer("core")
    assert first is second


def test_dictionary_is_shared_between_threads(app_context):
    "One Dictionary for the process -- loading it is the expensive part."
    _make_parser()
    seen = []
    lock = threading.Lock()

    def record():
        d = JapaneseSudachiParser._get_dictionary("core")
        with lock:
            seen.append(id(d))

    _run_threads(record, count=6)
    assert len(set(seen)) == 1, f"expected one shared dictionary, got {len(set(seen))}"


def test_each_thread_gets_its_own_tokenizer(app_context):
    "Threads must not share the Rust-backed Tokenizer object."
    _make_parser()
    seen = []
    lock = threading.Lock()

    def record():
        t = JapaneseSudachiParser._build_tokenizer("core")
        with lock:
            # Hold the reference: an id() on its own can be recycled by
            # the next thread once this one dies.
            seen.append(t)

    _run_threads(record, count=6)
    ids = [id(t) for t in seen]
    assert len(set(ids)) == 6, "each thread should build its own tokenizer"


def test_concurrent_parsing_succeeds(app_context):
    """
    Regression: parsing from several threads used to fail with
    RuntimeError("Already borrowed") -- about half the calls with two
    threads, most of them with twelve (the waitress worker count).
    """
    _make_parser()
    texts = ["私は元気です。", "本を読んでいます。", "日本語の勉強は楽しい。"]
    expected = {t: _tokens(t) for t in texts}
    errors = []
    results = []
    lock = threading.Lock()

    def work():
        try:
            for _ in range(20):
                for t in texts:
                    got = _tokens(t)
                    with lock:
                        results.append((t, got == expected[t]))
        except Exception as e:  # pylint: disable=broad-except
            with lock:
                errors.append(f"{type(e).__name__}: {e}")

    _run_threads(work, count=8)

    assert not errors, f"concurrent parsing raised: {errors[:3]}"
    assert results, "workers produced no results"
    assert all(ok for _, ok in results), "results differed between threads"


def test_concurrent_reading_and_lemma_succeeds(app_context):
    "get_reading and get_lemma go through the same tokenizer, same rules."
    _make_parser()
    current_settings()["japanese_reading"] = "hiragana"
    errors = []
    lock = threading.Lock()

    def work():
        try:
            p = JapaneseSudachiParser()
            for _ in range(20):
                assert p.get_reading("強い") == "つよい"
                assert p.get_lemma("広がっ") == "広がる"
        except Exception as e:  # pylint: disable=broad-except
            with lock:
                errors.append(f"{type(e).__name__}: {e}")

    _run_threads(work, count=8)
    assert not errors, f"concurrent reading/lemma raised: {errors[:3]}"


def _run_threads(fn, count):
    "Run fn in `count` threads, joined, re-raising nothing itself."
    threads = [threading.Thread(target=fn) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker thread hung"


# ---- caching ----


def test_is_supported_is_cached(app_context, monkeypatch):
    """
    The support check caches its result.  It used to share the cache-key
    attribute with the dictionary cache, which holds a different key
    format ("core|C" vs "core"), so the flag was never considered fresh
    and every call took the slow path.
    """
    assert JapaneseSudachiParser.is_supported() is True

    def _boom(_cls, _dict_type):
        raise AssertionError("is_supported() rebuilt the tokenizer")

    monkeypatch.setattr(JapaneseSudachiParser, "_build_tokenizer", classmethod(_boom))
    assert JapaneseSudachiParser.is_supported() is True


def test_invalidate_cache_forces_a_reload(app_context):
    "After a DB restore the dictionary is reloaded on next use."
    assert JapaneseSudachiParser.is_supported() is True
    first = JapaneseSudachiParser._get_dictionary("core")

    JapaneseSudachiParser._invalidate_cache()
    assert JapaneseSudachiParser._is_supported is None

    assert JapaneseSudachiParser.is_supported() is True
    assert JapaneseSudachiParser._get_dictionary("core") is not first


def test_parsed_tokens_carry_text_and_flags(app_context):
    "Sanity check on the ParsedToken fields used downstream."
    p = _make_parser()
    toks = p.get_parsed_tokens("元気です。", _japanese_language())
    assert [t.token for t in toks] == ["元気", "です", "。", "¶"]
    # 。 is in the language's sentence-splitting set, ¶ always ends one.
    assert [t.is_end_of_sentence for t in toks] == [False, False, True, True]
