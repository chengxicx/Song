"""
Parsing using pycantonese

The parser uses pycantonese for word segmentation and
Jyutping romanization.

Includes classes:

- CantoneseParser

"""

import re
from typing import List

import pycantonese
from lute.parse.base import ParsedToken, AbstractParser


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

    def get_reading(self, text: str):
        """
        Get the Jyutping for the given text.

        Returns None if the text has no romanizable characters
        (e.g. it is all punctuation or latin script).
        """
        pairs = pycantonese.characters_to_jyutping(text)
        if not pairs:
            return None

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
        ret = " ".join(parts)
        if ret == "":
            return None
        return ret
