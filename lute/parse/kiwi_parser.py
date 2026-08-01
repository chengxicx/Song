"""
Parsing for Korean using Kiwi (kiwipiepy).

Uses kiwipiepy (https://github.com/bab2min/kiwipiepy) package,
a fast Korean morphological analyzer based on the Kiwi C++ library.

Includes classes:

- KoreanParser

Kiwi provides:
- Morphological analysis with part-of-speech tagging
- Lemma (dictionary form) extraction
- Pronunciation/reading via the `form` and `orig` attributes

Korean word characters include Hangul syllables (가-힣), Jamo,
and Korean punctuation.  Korean is space-delimited but also
requires morphological analysis for proper tokenization, since
many particles and endings are written without spaces.
"""

import re
from typing import List

from lute.parse.base import ParsedToken, AbstractParser
from lute.settings.current import current_settings


class KoreanParser(AbstractParser):
    """
    Korean parser using kiwipiepy.

    This is only supported if kiwipiepy is installed.

    Configuration via UserSettings:
      - korean_reading: "" | "hangul" (empty = no reading)
        Currently readings are not automated beyond what Kiwi
        provides; the setting is reserved for future use.
    """

    _is_supported = None
    _instance = None
    _instance_key = None

    # ---- support detection ----

    @classmethod
    def is_supported(cls):
        """
        True if kiwipiepy can be imported and a Kiwi instance created.
        """
        if KoreanParser._is_supported is not None:
            return KoreanParser._is_supported

        try:
            cls._get_kiwi()
            KoreanParser._is_supported = True
        except Exception:  # pylint: disable=broad-except
            KoreanParser._is_supported = False

        return KoreanParser._is_supported

    # ---- Kiwi instance (cached) ----

    @classmethod
    def _get_kiwi(cls):
        """
        Build (or return cached) Kiwi instance.
        """
        from kiwipiepy import Kiwi  # pylint: disable=import-outside-toplevel

        if KoreanParser._instance is not None:
            return KoreanParser._instance

        kiwi = Kiwi()
        # Load default model.  Kiwi ships with a built-in model.
        KoreanParser._instance = kiwi
        return kiwi

    @classmethod
    def name(cls):
        return "Korean"

    # ---- parsing ----

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        "Parse the string using Kiwi."
        text = re.sub(r"[ \t]+", " ", text).strip()

        kiwi = self._get_kiwi()
        tokens = []

        for para in text.split("\n"):
            # split_into_sents=True returns a list of (sentence, tokens) tuples.
            # We use tokenize() directly for per-paragraph analysis.
            result = kiwi.tokenize(para)
            for m in result:
                surface = m.form
                if surface == "":
                    continue
                is_word = self._is_content_morph(m)
                is_eos = surface in language.regexp_split_sentences
                tokens.append(ParsedToken(surface, is_word, is_eos))
            # End-of-paragraph sentinel.
            tokens.append(ParsedToken("¶", False, True))

        return tokens

    # ---- POS helpers ----

    # Kiwi POS tags (subset relevant for content-word detection).
    # Full list: https://github.com/bab2min/kiwipiepy#pos-tags
    # Content words (자립어): NNG, NNP, NNB, NR, NP, VV, VA, VX, VCP, VCN,
    #   MM, MAG, MAJ, IC, SN, SH, SL, etc.
    # Bound morphemes (의존 명사/조사/어미): J*, E*, SP, SE, SF, SS, etc.
    _BOUND_POS_PREFIXES = (
        "J",   # 조사 (particles): JKS, JKC, JKG, JKO, JKB, JKV, JKQ, JX, JC
        "E",   # 어미 (endings): EP, EF, EC, ETN, ETM
        "SP",  # Space
        "SE",  # Ellipsis
        "SF",  # Sentence-ending punctuation (. ! ?)
        "SS",  # Brackets / quotes
        "SC",  # Comma / colon / etc.
        "SO",  # Connector (dash, etc.)
        "SY",  # Other symbols
    )

    # 의존 명사 (bound nouns) - start with "NNB" but some are independent.
    # We keep NNB as content words because Korean learners often want to
    # learn bound nouns like 것, 수, 때 as separate vocabulary.

    def _is_content_morph(self, morph) -> bool:
        """
        True if the morpheme represents a content morpheme worth
        keeping as a learnable token.
        """
        tag = morph.tag
        if not tag:
            return False
        # Check if the tag starts with any bound prefix.
        for prefix in self._BOUND_POS_PREFIXES:
            if tag.startswith(prefix):
                return False
        # SN (number), SL (foreign language letter), SH (Chinese char)
        # are content tokens.
        # SW is "other symbol" — skip it.
        if tag == "SW":
            return False
        return True

    # ---- reading ----

    def get_reading(self, text: str):
        """
        Get the pronunciation for the given text.

        Korean pronunciation is largely phonetic from the Hangul
        itself, so for now we return None.  This can be extended
        later with a pronunciation rules engine if needed.
        """
        ko_reading_setting = current_settings.get("korean_reading", "").strip()
        if ko_reading_setting == "":
            return None
        # Reserved for future romanization support.
        return None

    # ---- lemma ----

    def get_lemma(self, text: str):
        """
        Get the dictionary/lemma form of the given text.

        Kiwi's `lemma` attribute on TokenizedObject returns the
        dictionary form for verbs/adjectives (e.g. 먹었어 → 먹다).
        Only content morphemes are considered.
        """
        zws = "\u200B"
        text = text.replace(zws, "")

        kiwi = self._get_kiwi()
        result = kiwi.tokenize(text)

        lemmas = []
        for m in result:
            if not self._is_content_morph(m):
                continue
            surface = m.form
            # m.lemma gives the dictionary form for inflected words.
            # For non-inflected words (nouns, etc.), lemma == form.
            # Use getattr for safety across kiwipiepy versions.
            lemma = getattr(m, "lemma", None) or surface
            if lemma and lemma != "*":
                lemmas.append(lemma)
            else:
                lemmas.append(surface)

        if not lemmas:
            return None

        ret = "".join(lemmas).strip()
        if ret in ("", text):
            return None
        return ret
