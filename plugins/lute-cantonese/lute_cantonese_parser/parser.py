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
                is_word_char = re.match(pattern, word) is not None
                is_end_of_sentence = word in language.regexp_split_sentences
                tokens.append(ParsedToken(word, is_word_char, is_end_of_sentence))
            if i < len(lines) - 1:
                tokens.append(ParsedToken("¶", False, True))

        return tokens

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
