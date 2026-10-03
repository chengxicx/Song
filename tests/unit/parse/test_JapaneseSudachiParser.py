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
from typing import List, Optional, Tuple

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


def _context_readings(parser, text):
    "get_context_readings narrowed to its list form, asserting it exists."
    morphs = parser.get_context_readings(text)
    assert morphs is not None, f"expected context readings for {text!r}"
    readings: List[Tuple[str, Optional[str]]] = []
    readings.extend(morphs)
    return readings


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


def test_context_readings_follow_the_sentence(app_context):
    """
    get_context_readings reads each morpheme as the sentence around it
    disambiguates it.  A lone 一 is いち, but the 一 opening 一つ is ひと
    -- which is the whole reason the shadowing panel sends its sentence
    along instead of looking each token up on its own.
    """
    current_settings()["japanese_reading"] = "hiragana"
    p = _make_parser()

    assert p.get_reading("一") == "いち"

    morphs = _context_readings(p, "広い宇宙の、数ある一つ")
    assert ("広い", "ひろい") in morphs
    assert ("一", "ひと") in morphs
    # Kana morphemes read as themselves; punctuation has no reading.
    assert ("の", "の") in morphs
    assert ("、", None) in morphs


def test_context_readings_use_the_local_window_not_the_whole_sentence(app_context):
    """
    A morpheme's reading is settled from the window it forms with its
    immediate neighbours, not from the whole sentence: read as part of
    広い宇宙の、数ある一つ sudachi returns 数=スウ, while the same
    morphemes in a shorter window give the correct カズ.  This is the
    bug the shadowing panel showed as すう above 数 in 数ある.
    """
    current_settings()["japanese_reading"] = "hiragana"
    p = _make_parser()

    morphs = _context_readings(p, "広い宇宙の、数ある一つ")
    assert ("数", "かず") in morphs


def test_context_readings_keep_a_compound_together(app_context):
    """
    The window keeps the left neighbour, so a kanji that only reads the
    way it does because of the morpheme before it is not broken: 杯 is
    ばい in 一杯 (but さかずき alone) and 日 is にち in 一日 (but ひ
    alone).  Dropping the left neighbour is what would turn these into
    the standalone readings.
    """
    current_settings()["japanese_reading"] = "hiragana"
    p = _make_parser()

    assert p.get_reading("杯") == "さかずき"
    assert p.get_reading("日") == "ひ"

    assert ("杯", "ばい") in _context_readings(p, "一杯のコーヒー")
    assert ("日", "にち") in _context_readings(p, "一日が長かった")


def test_context_readings_need_the_reading_setting(app_context):
    "No japanese_reading setting means no furigana to give."
    current_settings()["japanese_reading"] = ""
    p = _make_parser()
    assert p.get_context_readings("広い宇宙の、数ある一つ") is None


def test_context_readings_skip_all_kana_text(app_context):
    "An all-kana sentence has nothing to annotate."
    current_settings()["japanese_reading"] = "hiragana"
    p = _make_parser()
    assert p.get_context_readings("あなたのもとへ") is None


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


def test_is_supported_does_not_build_the_dictionary(app_context, monkeypatch):
    """
    The support check runs for every parser at app start
    (lute.parse.registry.supported_parsers), so it must stay a package
    check.  Building the dictionary there cost ~33MB of private memory
    plus a read of the 200MB+ system.dic, even for users with no
    Japanese books.
    """

    def _boom(*_args, **_kwargs):
        raise AssertionError("is_supported() built the dictionary")

    monkeypatch.setattr(JapaneseSudachiParser, "_build_tokenizer", classmethod(_boom))
    monkeypatch.setattr(JapaneseSudachiParser, "_get_dictionary", classmethod(_boom))
    JapaneseSudachiParser._invalidate_cache()

    assert JapaneseSudachiParser.is_supported() is True


def test_parsed_tokens_carry_text_and_flags(app_context):
    "Sanity check on the ParsedToken fields used downstream."
    p = _make_parser()
    toks = p.get_parsed_tokens("元気です。", _japanese_language())
    assert [t.token for t in toks] == ["元気", "です", "。", "¶"]
    # 。 is in the language's sentence-splitting set, ¶ always ends one.
    assert [t.is_end_of_sentence for t in toks] == [False, False, True, True]
