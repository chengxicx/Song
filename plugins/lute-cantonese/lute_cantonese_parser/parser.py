"""
Parsing using pycantonese

The parser uses pycantonese for word segmentation and
Jyutping romanization.

Includes classes:

- CantoneseParser

"""

import re
from collections import Counter, defaultdict
from functools import lru_cache
from typing import List

from lute.parse.base import ParsedToken, AbstractParser


@lru_cache(maxsize=1)
def _corpus_char_readings():
    """
    character -> jyutping readings attested in the HKCanCor corpus,
    most common first.

    pycantonese's own tables keep a single reading per character, and
    its rime-cantonese data can override the corpus with a rarer sense
    (阿: attested aa3 x330, filed as o1), so a polyphone read the
    everyday way can disagree with the dictionary pick.  All built once
    per process (~0.3s); on any failure the map stays empty and the
    shadowing rescue degrades to the plain dictionary reading.
    """
    try:
        from pycantonese.corpus import hkcancor
        from pycantonese.jyutping.parse_jyutping import parse_jyutping

        counters = defaultdict(Counter)
        for token in hkcancor().tokens():
            word = token.word
            jyutping = token.jyutping
            if not word or not jyutping:
                continue
            try:
                parsed = parse_jyutping(jyutping)
            except ValueError:
                continue
            if len(word) != len(parsed):
                continue
            for char, syllable in zip(word, parsed):
                counters[char][str(syllable)] += 1
        return {
            char: tuple(reading for reading, _ in counter.most_common())
            for char, counter in counters.items()
        }
    except Exception:  # pylint: disable=broad-exception-caught
        return {}


class CantoneseParser(AbstractParser):
    """
    A parser for Cantonese, using the pycantonese library
    for text segmentation and Jyutping readings.

    pycantonese segments text using longest-string matching trained
    on the HKCanCor corpus and the rime-cantonese word list.
    """

    @classmethod
    def name(cls):
        return "Lute Cantonese"

    @classmethod
    def languages(cls):
        "Language names this parser is designed for."
        return {"cantonese", "廣東話", "粤语", "粵語"}

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        """
        Returns ParsedToken array for given language.
        """
        # Imported here, not at module level: the app loads every
        # parser plugin at start-up (lute.parse.registry) and just
        # importing pycantonese costs ~45MB of resident memory.
        import pycantonese  # pylint: disable=import-outside-toplevel

        # Ensure standard carriage returns so that paragraph
        # markers are used correctly.  Lute uses paragraph markers
        # for rendering.
        text = text.replace("\r\n", "\n")

        tokens = []
        pattern = f"[{language.word_characters}]"

        # pycantonese.segment strips all whitespace, so newlines
        # must be handled before segmentation: each line is
        # segmented on its own, and each newline becomes the
        # paragraph marker "¶".
        lines = text.split("\n")
        for i, line in enumerate(lines):
            for word in pycantonese.segment(line):
                # Some pycantonese versions may group sentence
                # punctuation into a word token (e.g. "吃饭了吗？现在是").
                # Split each token into runs of word chars and runs of
                # punctuation so end-of-sentence chars always stand alone.
                for piece in self._split_token(word, language):
                    is_word_char = re.match(pattern, piece) is not None
                    is_end_of_sentence = piece in language.regexp_split_sentences
                    tokens.append(ParsedToken(piece, is_word_char, is_end_of_sentence))
            if i < len(lines) - 1:
                tokens.append(ParsedToken("¶", False, True))

        return tokens

    @staticmethod
    def _split_token(word, language):
        """
        Split a segmented token into alternating runs of word
        characters and non-word characters.
        """
        return re.findall(
            f"[{language.word_characters}]+|[^{language.word_characters}]+", word
        )

    @staticmethod
    def _pairs_to_reading(pairs):
        """
        (pycantonese jyutping pairs) -> one reading string, or None when
        nothing in it was romanizable (e.g. all punctuation or latin
        script): unromanizable pieces keep their raw characters.
        """
        parts = []
        has_jyutping = False
        for word, jyutping in pairs:
            if jyutping:
                parts.append(jyutping)
                has_jyutping = True
            else:
                parts.append(word)
        if not has_jyutping:
            return None
        ret = " ".join(parts).strip()
        return ret or None

    def get_reading(self, text: str):
        """
        Get the Jyutping for the given text.

        Returns None if the text has no romanizable characters
        (e.g. it is all punctuation or latin script).
        """
        import pycantonese  # pylint: disable=import-outside-toplevel

        return self._pairs_to_reading(pycantonese.characters_to_jyutping(text))

    def get_readings(self, text: str):
        """
        All plausible jyutping readings of the text, dictionary pick first.

        pycantonese keeps a single reading per word, and for a polyphone
        that pick can be the rarer sense: 阿 is filed under o1, so the
        name prefix 阿明 comes back "o1 ming4" while it is actually read
        "aa3 ming4".  The corpus attests the readings characters really
        take, so this also yields the per-character reading with each
        attested alternate substituted in, one at a time.  The shadowing
        fuzzy rescue compares a misread against every candidate, so a
        correctly pronounced polyphone is not scored as a miss.

        Returns a deduplicated list; empty when nothing is romanizable.
        """
        import pycantonese  # pylint: disable=import-outside-toplevel

        out = []

        def add(reading):
            if reading and reading not in out:
                out.append(reading)

        default = self._pairs_to_reading(pycantonese.characters_to_jyutping(text))
        add(default)

        # Per-character: a list input skips word segmentation, so each
        # char stands alone.  Only offered when the text romanizes as a
        # whole, so punctuation-only, foreign and half-romanizable text
        # yield no candidates here either.
        if default is not None:
            chars = [ch for ch in (text or "") if ch.strip()]
            per_char = pycantonese.characters_to_jyutping(list(chars))
            base = [jyutping for _, jyutping in per_char]
            add(" ".join(base).strip() or None)
            attested = _corpus_char_readings()
            for i, reading in enumerate(base):
                for alt in attested.get(chars[i], ()):
                    if alt != reading:
                        variant = list(base)
                        variant[i] = alt
                        add(" ".join(variant).strip() or None)
        return out
