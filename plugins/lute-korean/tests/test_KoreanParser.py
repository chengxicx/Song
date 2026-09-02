"""
KoreanParser plugin tests.

Also verifies the graceful-degradation behaviour: language objects
without the kiwi_* attributes (e.g. plain upstream Lute Language
models) fall back to the documented defaults.
"""

from types import SimpleNamespace

import pytest

from lute.parse.base import ParsedToken

from lute_korean_parser.parser import KoreanParser


def _minimal_language(**kwargs):
    """
    A minimal language stand-in without any kiwi_* attributes,
    simulating an upstream Lute Language model.
    """
    return SimpleNamespace(regexp_split_sentences=".!?。？！", **kwargs)


def _word_tokens(tokens):
    "Return (surface, is_word) for non-space, non-paragraph tokens."
    ret = []
    for t in tokens:
        if t.token in (" ", "¶"):
            continue
        ret.append((t.token, t.is_word))
    return ret


def _surfaces(tokens):
    return [t.token for t in tokens if t.token not in (" ", "¶")]


@pytest.fixture(name="parser")
def fixture_parser():
    return KoreanParser()


def test_parser_is_supported(parser):
    "kiwipiepy must be importable for the plugin to work."
    assert KoreanParser.is_supported() is True


def test_default_morpheme_tokenization(parser, korean):
    "Default mode splits into morphemes, keeps spaces, marks sentence end."
    tokens = parser.get_parsed_tokens("예상했었는데 먹었어.", korean)

    ss = _surfaces(tokens)
    # kiwipiepy 0.23.x: 예상/NNG + 하/XSV + 었었/EP + 는데/EC + 먹/VV + 었/EP + 어/EF + ./SF
    assert "예상" in ss
    assert "었었" in ss
    assert "는데" in ss
    assert "먹" in ss
    # Space between 어절 is a non-word token.
    spaces = [t for t in tokens if t.token == " "]
    assert len(spaces) == 1
    assert spaces[0].is_word is False
    # The sentence-final punctuation ends a sentence.
    eos = [t for t in tokens if t.token == "."]
    assert len(eos) == 1
    assert eos[0].is_end_of_sentence is True
    # Paragraph sentinel present.
    assert any(t.token == "¶" for t in tokens)


def test_lemma_mode_merges_predicate(parser, korean):
    "In lemma mode, inflected predicates appear as dictionary forms."
    korean.kiwi_tokenizer_mode = "lemma"
    tokens = parser.get_parsed_tokens("예상했었는데 먹었어.", korean)
    ss = _surfaces(tokens)
    assert "예상하다" in ss
    assert "먹다" in ss


def test_eojeol_mode_keeps_whole_block(parser, korean):
    "In eojeol mode, whitespace blocks stay single tokens."
    korean.kiwi_tokenizer_mode = "eojeol"
    tokens = parser.get_parsed_tokens("예상했었는데 먹었어.", korean)
    ss = _surfaces(tokens)
    # Each 어절 becomes exactly one token.  Note kiwipiepy 0.23.x
    # decomposes 했 into 하 + 었었 morphs, so the joined surface is
    # '예상하었었는데' rather than the raw text - pinned to this
    # kiwi version, like the cantonese plugin pins pycantonese.
    assert ss == ["예상하었었는데", "먹었어."]


def test_get_lemma_stemming(parser, korean):
    "Default stemming=True: inflected forms resolve to dictionary form."
    assert parser.get_lemma("먹었어", korean) == "먹다"
    assert parser.get_lemma("예상했었는데", korean) == "예상하다"


def test_get_lemma_stemming_disabled(parser):
    "kiwi_stemming=False disables lemmatization."
    lang = SimpleNamespace(kiwi_stemming=False)
    assert parser.get_lemma("먹었어", lang) is None


def test_get_lemma_without_language(parser):
    "No language object: stemming defaults to on."
    assert parser.get_lemma("먹었어") == "먹다"


def test_filter_particles(parser, korean):
    "kiwi_filter_particles=True makes J*/E* morphemes non-word."
    korean.kiwi_filter_particles = True
    tokens = parser.get_parsed_tokens("나는 학교에 간다", korean)
    wt = dict(_word_tokens(tokens))
    assert wt.get("는") is False
    assert wt.get("에") is False
    assert wt.get("학교") is True


def test_fallback_language_without_kiwi_attributes(parser):
    "A language object with no kiwi_* attrs uses the documented defaults."
    lang = _minimal_language()
    tokens = parser.get_parsed_tokens("예상했었는데 먹었어.", lang)
    ss = _surfaces(tokens)
    assert "예상" in ss  # morpheme mode (default)
    assert parser.get_lemma("먹었어", lang) == "먹다"  # stemming on (default)


def test_multi_paragraph_parsing(parser, korean):
    "Each paragraph gets its own ¶ sentinel."
    tokens = parser.get_parsed_tokens("먹었어.\n간다.", korean)
    assert sum(1 for t in tokens if t.token == "¶") == 2
