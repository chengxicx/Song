"""
Parsing for Korean using Kiwi (kiwipiepy).

Uses kiwipiepy (https://github.com/bab2min/kiwipiepy) package,
a fast Korean morphological analyzer based on the Kiwi C++ library.

Includes classes:

- KoreanParser

Korean is a space-delimited language: words (어절) are separated by
spaces, and each word may consist of multiple morphemes (e.g. 먹었다 =
먹 + 었 + 다).  This parser splits text at the word level using the
language's word_characters, preserving spaces naturally.  Kiwi is
used internally for lemma (dictionary form) extraction, so that
inflected forms like 먹었다 resolve to the dictionary form 먹다.
"""

from lute.parse.space_delimited_parser import SpaceDelimitedParser
from lute.settings.current import current_settings


class KoreanParser(SpaceDelimitedParser):
    """
    Korean parser using kiwipiepy for morphological analysis.

    Text is split at the word level (space-delimited), preserving
    Korean word boundaries and spacing.  Kiwi is used internally
    for lemma extraction (e.g. 먹었다 → 먹다).

    This is only supported if kiwipiepy is installed.
    """

    _is_supported = None
    _instance = None

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
        KoreanParser._instance = kiwi
        return kiwi

    @classmethod
    def name(cls):
        return "Korean"

    # ---- POS helpers (used by get_lemma) ----

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

    def _is_content_morph(self, morph) -> bool:
        """
        True if the morpheme represents a content morpheme worth
        keeping for lemma extraction.
        """
        tag = morph.tag
        if not tag:
            return False
        for prefix in self._BOUND_POS_PREFIXES:
            if tag.startswith(prefix):
                return False
        if tag == "SW":
            return False
        return True

    # ---- reading ----

    def get_reading(self, text: str):
        """
        Get the pronunciation for the given text.

        Korean pronunciation is largely phonetic from the Hangul
        itself, so for now we return None.  This can be extended
        later with a romanization engine if needed.
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

        Kiwi's `lemma` attribute returns the dictionary form for
        inflected words (e.g. 먹었어 → 먹다).  Only content
        morphemes are considered; particles and endings are skipped.
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
