"""
Parsing for Korean using Kiwi (kiwipiepy).

Uses kiwipiepy (https://github.com/bab2min/kiwipiepy) package,
a fast Korean morphological analyzer based on the Kiwi C++ library.

Includes classes:

- KoreanParser

Korean text is analyzed morpheme-by-morpheme using Kiwi, similar to how
MeCab analyzes Japanese.  Each morpheme (예상, 하, 었, 는데, etc.) becomes
a separate clickable token.  Spaces between 어절 are preserved as
non-word tokens so the original text layout is maintained.

Kiwi's `lemma` attribute is used for dictionary-form extraction, so that
inflected forms resolve to their base form for parent/term linking.
"""

import re
from typing import List

from lute.parse.base import ParsedToken, AbstractParser
from lute.settings.current import current_settings


class KoreanParser(AbstractParser):
    """
    Korean parser using kiwipiepy for morphological analysis.

    Text is split into individual morphemes using Kiwi, similar to how
    MeCab splits Japanese text.  Each content morpheme (noun, verb,
    particle, ending, etc.) becomes a separate clickable word token.
    Punctuation and symbols are non-word tokens.

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

    # ---- POS helpers ----

    # Kiwi POS tags that represent punctuation/symbols (non-word tokens).
    # Full tag list: https://github.com/bab2min/kiwipiepy#pos-tags
    _SYMBOL_TAGS = frozenset({
        "SF",  # Sentence-ending punctuation (. ! ?)
        "SP",  # Space
        "SS",  # Brackets / quotes
        "SC",  # Comma / colon / etc.
        "SE",  # Ellipsis
        "SO",  # Connector (dash, etc.)
        "SY",  # Other symbols
        "SW",  # Other symbols / emojis
    })

    # POS tag prefixes for bound morphemes (조사/어미) that are skipped
    # during lemma extraction.
    _BOUND_POS_PREFIXES = (
        "J",   # 조사 (particles): JKS, JKC, JKG, JKO, JKB, JKV, JKQ, JX, JC
        "E",   # 어미 (endings): EP, EF, EC, ETN, ETM
        "SP", "SE", "SF", "SS", "SC", "SO", "SY", "SW",
    )

    # Sentence-ending punctuation tags.
    _SENTENCE_END_TAGS = frozenset({"SF"})

    def _is_word_morph(self, morph) -> bool:
        """
        True if the morpheme should be a clickable word.

        All morphemes are words except punctuation/symbols.
        This is consistent with how MeCab handles Japanese (all
        morphemes are words except symbols).
        """
        tag = morph.tag
        if not tag:
            return True
        return tag not in self._SYMBOL_TAGS

    def _is_content_morph(self, morph) -> bool:
        """
        True if the morpheme is a content morpheme worth keeping
        for lemma extraction (skips particles, endings, symbols).
        """
        tag = morph.tag
        if not tag:
            return False
        for prefix in self._BOUND_POS_PREFIXES:
            if tag.startswith(prefix):
                return False
        return True

    # ---- tokenization ----

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        """
        Parse text into morpheme-level tokens using Kiwi.

        Each morpheme (예상, 하, 었, 는데, etc.) becomes a separate
        ParsedToken.  Spaces between 어절 are preserved as non-word
        tokens to maintain the original text layout.
        """
        text = re.sub(r"[ \t]+", " ", text).strip()

        # Build the set of sentence-ending characters from language settings.
        splitchar = ""
        if language and language.regexp_split_sentences:
            splitchar = language.regexp_split_sentences.strip()
        if not splitchar:
            splitchar = ".!?。？！"

        kiwi = self._get_kiwi()
        tokens = []

        for para in text.split("\n"):
            para = para.strip()
            if para:
                morphs = kiwi.tokenize(para)

                prev_end = 0
                for m in morphs:
                    # Insert a space token if there's a gap between
                    # the end of the previous morpheme's surface form
                    # and the start of this morpheme.  Overlapping
                    # morphemes (same 어절) don't get a space.
                    if m.start > prev_end:
                        tokens.append(ParsedToken(" ", False, False))

                    form = m.form
                    is_word = self._is_word_morph(m)
                    is_eos = m.tag in self._SENTENCE_END_TAGS or any(
                        c in splitchar for c in form
                    )
                    tokens.append(ParsedToken(form, is_word, is_eos))

                    # Advance prev_end, but never go backwards
                    # (handles overlapping morphemes).
                    new_end = m.start + m.len
                    if new_end > prev_end:
                        prev_end = new_end

            # End-of-paragraph sentinel.
            tokens.append(ParsedToken("¶", False, True))

        return tokens

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
        return None

    # ---- lemma ----

    def get_lemma(self, text: str):
        """
        Get the dictionary/lemma form of the given text.

        With morpheme-level splitting, this is typically called for
        individual morphemes.  Kiwi's `lemma` attribute returns the
        dictionary form for inflected words (e.g. 먹었어 → 먹다).
        For multi-morpheme text (e.g. multi-word terms), content
        morpheme lemmas are joined.

        Returns None if the text is already in its base form, is a
        grammatical morpheme, or can't be determined.
        """
        zws = "\u200B"
        text = text.replace(zws, "")

        if not text.strip():
            return None

        kiwi = self._get_kiwi()
        result = kiwi.tokenize(text)

        if not result:
            return None

        lemmas = []
        for m in result:
            if not self._is_content_morph(m):
                continue
            surface = m.form
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
