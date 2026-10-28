"""
Grammar-engine memory lifetime: lazy load, idle sweep, release on freeze.

A loaded spaCy pipeline is 40-200 MB, and until this change the only
reference to it lived inside the closure returned by
make_spacy_tokenizer -- nobody could release it, so a language's model
stayed resident for the whole life of the process once its grammar panel
had been opened.  These tests pin the contract that replaced it:

* one load per model, even when several threads ask at the same moment;
* a released model reloads transparently on the next analysis;
* only pipelines idle past the TTL are swept, and the sweep runs from
  the read blueprint so plain reading does not pin a model forever;
* freezing a language releases its pipeline; thawing preloads nothing.

spaCy is faked through sys.modules, so these run without the optional
extras -- which they must, since CI installs no engine extras at all.
"""

import sys
import threading

import pytest

from lute.db import db
from lute.models.language import Language
from lute.read.render import grammar_analysis
from lute.read.render import grammar_analysis_matcher as matcher


# ---- fake spaCy --------------------------------------------------------


class _Morph:
    "Stand-in for spaCy's MorphAnalysis."

    def to_dict(self):
        "The engines call this to read UD features."
        return {}


class _Token:
    "Stand-in for a spaCy Token."

    def __init__(self, text, idx):
        self.text = text
        self.lemma_ = text.lower()
        self.pos_ = "NOUN"
        self.morph = _Morph()
        self.idx = idx


def _fake_nlp(sentence):
    "One token per whitespace-separated word; enough for the matcher."
    return [_Token(w, i) for i, w in enumerate(sentence.split())]


class _FakeSpacy:
    "Minimal sys.modules stand-in that records every load()."

    def __init__(self):
        self.loads = []
        self._lock = threading.Lock()

    def load(self, name, exclude=None):  # noqa: ARG002 - mirrors spaCy
        with self._lock:
            self.loads.append(name)
        return _fake_nlp


@pytest.fixture(name="fake_spacy")
def fixture_fake_spacy(monkeypatch):
    """
    Fake spaCy plus an isolated model registry.

    The registry is process-wide and the engine modules created their
    holders at import time, so it is snapshotted and restored rather
    than merely cleared -- otherwise those holders would be orphaned for
    the rest of the session.
    """
    fake = _FakeSpacy()
    monkeypatch.setitem(sys.modules, "spacy", fake)
    saved = dict(matcher._MODELS)
    matcher._MODELS.clear()
    yield fake
    matcher._MODELS.clear()
    matcher._MODELS.update(saved)


# ---- load / release / sweep -------------------------------------------


def test_a_model_loads_once_and_is_reused(fake_spacy):
    tokens_for = matcher.make_spacy_tokenizer("xx_test_model")
    assert tokens_for("one two")[0]["surface"] == "one"
    assert tokens_for("three")[0]["surface"] == "three"
    assert fake_spacy.loads == ["xx_test_model"]
    assert matcher.loaded_spacy_models() == ["xx_test_model"]


def test_concurrent_first_use_loads_only_once(fake_spacy):
    "Two readers opening the panel together used to load two pipelines."
    tokens_for = matcher.make_spacy_tokenizer("xx_test_model")
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        tokens_for("one two")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert fake_spacy.loads == ["xx_test_model"]


def test_release_drops_it_and_the_next_call_reloads(fake_spacy):
    tokens_for = matcher.make_spacy_tokenizer("xx_test_model")
    tokens_for("one")

    assert matcher.release_spacy_model("xx_test_model") is True
    assert matcher.loaded_spacy_models() == []

    assert tokens_for("two")[0]["surface"] == "two"
    assert fake_spacy.loads == ["xx_test_model", "xx_test_model"]


def test_releasing_something_not_loaded_is_a_no_op(fake_spacy):
    matcher.make_spacy_tokenizer("xx_test_model")  # registered, never used
    assert matcher.release_spacy_model("xx_test_model") is False
    assert matcher.release_spacy_model("xx_absent") is False


def test_release_hands_the_pages_back(monkeypatch, fake_spacy):
    """
    Dropping the reference is not enough on glibc -- the freed pages stay
    in the process's arenas, so a freeze/reload cycle grows RSS instead of
    returning it.  A real release must follow up with the reclaim step.
    """
    trims = []
    monkeypatch.setattr(matcher, "_reclaim", lambda: trims.append(1))

    matcher.make_spacy_tokenizer("xx_test_model")("a")
    assert trims == []

    assert matcher.release_spacy_model("xx_test_model") is True
    assert trims == [1]

    # Nothing was resident, so there is nothing to reclaim: a no-op release
    # must not pay for a collection pause.
    assert matcher.release_spacy_model("xx_test_model") is False
    assert trims == [1]


def test_reclaim_survives_a_host_without_glibc():
    "No libc.so.6 off Linux; the reclaim step must be a no-op, not a crash."
    matcher._reclaim()


# ---- periodic arena trim ----------------------------------------------
#
# `_last_trim` is pushed to -1e6 rather than 0.0 below: the guard is
# `time.monotonic() - _last_trim < interval`, and monotonic() counts from
# boot -- on a freshly started CI runner it can still be under the 600s
# interval, so "0.0" did not mean "long ago" there.


def test_trim_runs_once_per_interval(monkeypatch):
    """
    Per-request hook, so it must be free until the interval is up: one
    timestamp comparison per request, a trim at most every few minutes.
    """
    trims = []
    monkeypatch.setattr(matcher, "_trim", lambda: trims.append(1))
    monkeypatch.setattr(matcher, "_last_trim", -1e6)

    assert matcher.trim_arenas(600) is True
    assert trims == [1]

    assert matcher.trim_arenas(600) is False
    assert trims == [1]

    # Pretend the interval elapsed.
    monkeypatch.setattr(matcher, "_last_trim", -1e6)
    assert matcher.trim_arenas(600) is True
    assert trims == [1, 1]


def test_trim_needs_no_model_and_collects_nothing(monkeypatch):
    """
    The periodic trim is not a release: nothing was dropped, so it skips
    the full gc.collect() pause and only asks malloc for its pages back.
    """
    calls = []
    monkeypatch.setattr(matcher.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(matcher, "_trim", lambda: calls.append("trim"))
    monkeypatch.setattr(matcher, "_last_trim", -1e6)

    assert matcher.trim_arenas(600) is True
    assert calls == ["trim"]


def test_release_still_collects_before_trimming(monkeypatch, fake_spacy):
    "A release does need the collection: spaCy's pipelines carry cycles."
    calls = []
    monkeypatch.setattr(matcher.gc, "collect", lambda: calls.append("gc"))
    monkeypatch.setattr(matcher, "_trim", lambda: calls.append("trim"))

    matcher.make_spacy_tokenizer("xx_test_model")("a")
    assert matcher.release_spacy_model("xx_test_model") is True
    assert calls == ["gc", "trim"]


def test_read_request_trims_the_arenas(app, client, monkeypatch):
    "The read blueprint's hook is what keeps RSS from ratcheting up."
    trims = []
    monkeypatch.setattr(matcher, "_trim", lambda: trims.append(1))
    monkeypatch.setattr(matcher, "_last_trim", -1e6)

    client.get("/read/empty")

    assert trims == [1]


def test_idle_sweep_drops_the_cold_model_only(fake_spacy):
    cold = matcher.make_spacy_tokenizer("xx_cold")
    warm = matcher.make_spacy_tokenizer("xx_warm")
    cold("a")
    warm("b")

    matcher._MODELS["xx_cold"].last_used -= 10_000

    assert matcher.release_idle_spacy_models(900) == ["xx_cold"]
    assert matcher.loaded_spacy_models() == ["xx_warm"]


def test_idle_sweep_spares_everything_before_the_ttl(fake_spacy):
    matcher.make_spacy_tokenizer("xx_test_model")("a")
    assert matcher.release_idle_spacy_models(900) == []
    assert matcher.loaded_spacy_models() == ["xx_test_model"]


def test_read_request_sweeps_idle_models(app, client, fake_spacy):
    "A sweep hook on the read blueprint -- reading alone must not pin it."
    matcher.make_spacy_tokenizer("xx_test_model")("hello")
    matcher._MODELS["xx_test_model"].last_used -= 10_000

    client.get("/read/empty")

    assert matcher.loaded_spacy_models() == []


# ---- which languages have a releasable model ---------------------------


class _Lang:
    "Language stand-in: the detectors read name and parser_type only."

    def __init__(self, name, parser_type=""):
        self.name = name
        self.parser_type = parser_type


@pytest.mark.parametrize(
    "name,expected",
    [
        ("English", "en_core_web_sm"),
        ("Spanish", "es_core_news_md"),
        ("French", "fr_core_news_sm"),
        ("German", "de_core_news_sm"),
        ("Italian", "it_core_news_sm"),
        ("Portuguese", "pt_core_news_sm"),
        # No spaCy pipeline: these ride on another tokenizer, are pure
        # regex, or are too small to be worth unloading.
        ("Japanese", None),
        ("Korean", None),
        ("Russian", None),
        ("Thai", None),
        ("Arabic", None),
        ("Turkish", None),
    ],
)
def test_spacy_model_for_reads_the_engine_table(name, expected):
    assert grammar_analysis.spacy_model_for(_Lang(name)) == expected


# ---- freeze / thaw through the language route --------------------------


@pytest.fixture(name="releases")
def fixture_releases(monkeypatch):
    "Record freeze-time releases instead of touching real models."
    calls = []

    def _release(language):
        calls.append(language.name)
        return True

    monkeypatch.setattr(grammar_analysis, "release_grammar_engine", _release)
    return calls


def test_freezing_a_language_releases_its_engine(app, client, english, releases):
    lang_id = english.id

    assert client.post(f"/language/toggle_active/{lang_id}").status_code == 302

    assert db.session.get(Language, lang_id).is_active is False
    assert releases == ["English"]


def test_thawing_does_not_preload(app, client, english, releases):
    "Un-freezing five languages should not load five models."
    lang_id = english.id
    client.post(f"/language/toggle_active/{lang_id}")  # freeze
    client.post(f"/language/toggle_active/{lang_id}")  # thaw

    assert db.session.get(Language, lang_id).is_active is True
    assert releases == ["English"], "only the freeze released anything"


def test_freezing_really_drops_a_loaded_pipeline(app, client, english, fake_spacy):
    "End to end: the model is gone after the freeze, back on next use."
    tokens_for = matcher.make_spacy_tokenizer("en_core_web_sm")
    tokens_for("hello world")
    assert matcher.loaded_spacy_models() == ["en_core_web_sm"]

    client.post(f"/language/toggle_active/{english.id}")

    assert matcher.loaded_spacy_models() == []
    tokens_for("hello again")
    assert matcher.loaded_spacy_models() == ["en_core_web_sm"]
