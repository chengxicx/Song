"""
Which dictionary-form source a language gets.

Nothing here needs a grammar engine: the spaCy and pymorphy3 branches are
driven through stubs, so these tests run on a base install (both engines
are optional extras).
"""

import pytest

from lute.parse.space_delimited_parser import SpaceDelimitedParser
from lute.term import lemma_provider
from lute.term.lemma_provider import _word_count, lemma_provider_for


class _Lang:
    "The parts of a Language that lemma_provider_for() reads."

    def __init__(self, name, parser_type="spacedel", parser=None):
        self.name = name
        self.parser_type = parser_type
        self.parser = parser if parser is not None else SpaceDelimitedParser()


class _BothHalvesParser(SpaceDelimitedParser):
    "A parser that answers both questions, like Sudachi does."

    @classmethod
    def name(cls):
        return "Stub Parser"

    def get_lemma(self, text):
        return text[:-1] if text.endswith("s") else None

    def content_token_count(self, text):
        return len(text.split())


class _LemmaOnlyParser(SpaceDelimitedParser):
    "get_lemma() but no content_token_count(): the MeCab / Korean shape."

    def get_lemma(self, text):
        return text


def _no_spacy(language):  # pylint: disable=unused-argument
    "spacy_model_for() stand-in: no language here has a spaCy pipeline."
    return None


def test_parser_that_answers_both_questions_wins(monkeypatch):
    """
    Japanese keeps coming from the parser.

    The engine branches must not even be probed: the parser's own answer
    is the one the existing ja backfill was validated against.
    """
    probed = []

    def _record(name):
        probed.append(name)
        return True

    monkeypatch.setattr(lemma_provider, "_installed", _record)

    provider = lemma_provider_for(
        _Lang("Japanese", "japanese_sudachi", _BothHalvesParser())
    )

    assert provider is not None
    assert provider.label == "Stub Parser"
    assert provider.lemma_of("perros") == "perro"
    assert provider.content_token_count("el perro") == 2
    assert probed == []


def test_lemma_without_content_count_is_refused():
    """
    MeCab and the Korean plugin have this shape.

    They were refused before this module existed, and they still are:
    without a content-word count the "lemma is a concatenation" filter
    cannot run, and silently skipping that filter is how 似ている ->
    似るいる gets created.
    """
    assert lemma_provider_for(_Lang("Japanese", "japanese", _LemmaOnlyParser())) is None


def test_spacedel_language_uses_spacy(monkeypatch):
    "A language with a grammar engine gets that engine's lemma."
    monkeypatch.setattr(lemma_provider, "_installed", lambda name: True)
    monkeypatch.setattr(
        "lute.read.render.grammar_analysis.spacy_model_for",
        lambda language: "xx_core_test",
    )

    def _fake_tokenizer(model):
        assert model == "xx_core_test"

        def _tokens_for(text):
            if text == "perros":
                return [{"surface": "perros", "lemma": "perro"}]
            return [
                {"surface": "el", "lemma": "el"},
                {"surface": "perro", "lemma": "perro"},
            ]

        return _tokens_for

    monkeypatch.setattr(
        "lute.read.render.grammar_analysis_matcher.make_spacy_tokenizer",
        _fake_tokenizer,
    )

    provider = lemma_provider_for(_Lang("Spanish"))

    assert provider is not None
    assert provider.label == "spaCy (xx_core_test)"
    assert provider.lemma_of("perros") == "perro"
    # A multi-word term gets no lemma: joining one would be the
    # concatenation this tool must not create.
    assert provider.lemma_of("el perro") is None
    assert provider.content_token_count("el perro") == 2


def test_missing_engine_is_refused(monkeypatch):
    "The extra is not installed: refuse, rather than produce an empty plan."
    monkeypatch.setattr(lemma_provider, "_installed", lambda name: False)
    monkeypatch.setattr(
        "lute.read.render.grammar_analysis.spacy_model_for",
        lambda language: "xx_core_test",
    )

    assert lemma_provider_for(_Lang("Spanish")) is None


def test_russian_uses_pymorphy3(monkeypatch):
    "Russian has no spaCy pipeline; pymorphy3 supplies the normal form."
    monkeypatch.setattr(lemma_provider, "_installed", lambda name: True)
    monkeypatch.setattr("lute.read.render.grammar_analysis.spacy_model_for", _no_spacy)

    class _Parse:
        normal_form = "собака"

    class _Analyzer:
        def parse(self, word):
            return [_Parse()] if word else []

    monkeypatch.setattr(
        "lute.read.render.grammar_analysis_ru.morph_analyzer", lambda: _Analyzer()
    )

    provider = lemma_provider_for(_Lang("Russian"))

    assert provider is not None
    assert provider.label == "pymorphy3"
    assert provider.lemma_of("собаки") == "собака"
    assert provider.lemma_of("две собаки") is None


def test_unknown_language_has_no_provider(monkeypatch):
    "Thai, Chinese and the ~60 definition-only languages land here."
    monkeypatch.setattr(lemma_provider, "_installed", lambda name: True)
    monkeypatch.setattr("lute.read.render.grammar_analysis.spacy_model_for", _no_spacy)

    assert lemma_provider_for(_Lang("Thai", "lute_thai")) is None
    assert lemma_provider_for(_Lang("Klingon")) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("perros", 1),
        ("el perro", 2),
        ("  ", 0),
        ("", 0),
        # A multi-token term carries zero-width spaces, not spaces.
        ("el\u200Bperro", 2),
    ],
)
def test_word_count(text, expected):
    "The single-word gate for space-delimited languages."
    assert _word_count(text) == expected
