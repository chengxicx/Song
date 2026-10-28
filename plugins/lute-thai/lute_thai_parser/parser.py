"""
Parsing using pythainlp

Includes classes:

- ThaiParser

"""

import re
import os

from typing import List

from lute.parse.base import ParsedToken, AbstractParser


class ThaiParser(AbstractParser):
    """
    A parser for Thai that uses the pythainlp library for text segmentation.

    The user can add some exceptions to the "parsing_exceptions.txt"
    data file.
    """

    @classmethod
    def name(cls):
        return "Lute Thai"

    @classmethod
    def uses_data_directory(cls):
        "Uses the data_directory (defined in the AbstractParser)."
        return False

    # @classmethod
    # def init_data_directory(cls):
    #     "Set up necessary files."
    #     pass

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        """
        Returns ParsedToken array for given language.
        """
        # Imported here, not at module level: the app loads every
        # parser plugin at start-up (lute.parse.registry) and just
        # importing pythainlp costs ~220MB of resident memory.
        import pythainlp  # pylint: disable=import-outside-toplevel

        text = text.replace("\r\n", "\n")

        words = pythainlp.word_tokenize(text)
        tokens = []
        pattern = f"[{language.word_characters}]"
        whitespace_regex = r"[ \t]+"

        # Spaces do double duty in Thai, and the text's own style
        # decides which role they play.  Traditionally written Thai
        # puts spaces only between sentences (there is no full stop),
        # so a space ends one.  LingQ-style stories, though, are
        # "word-spaced": every word is separated by a space and one
        # line is one sentence.  Treating those spaces as sentence
        # ends shatters the text into one sentence per word -- every
        # word got its own .textsentence, its own 🔊 button, and the
        # sentence translate / grammar panel saw single words.  So
        # measure the space density once per text: when most words
        # are followed by a space, spaces are word separators and
        # only newlines and sentence punctuation (ฯ ! ?) end a
        # sentence.  Traditional text (spaces rare) keeps the old
        # behaviour.
        word_count = sum(1 for w in words if re.match(pattern, w))
        space_count = sum(1 for w in words if re.match(whitespace_regex, w))
        spaces_are_word_separators = word_count > 0 and space_count / word_count >= 0.5

        for word in words:
            is_word_char = re.match(pattern, word) is not None
            is_whitespace = re.match(whitespace_regex, word) is not None
            is_split_sentence = word in language.regexp_split_sentences
            is_end_of_sentence = is_split_sentence or (
                is_whitespace and not spaces_are_word_separators
            )
            if is_end_of_sentence:
                is_word_char = False
            if word == "\n":
                word = "¶"
            if word == "¶":
                is_word_char = False
                is_end_of_sentence = True
            t = ParsedToken(word, is_word_char, is_end_of_sentence)
            tokens.append(t)
        return tokens

    def get_reading(self, text: str):
        """
        Get the RTGS (Royal Thai General System of Transcription)
        romanization for the given text, used as the reading above a
        Thai word.  Each syllable carries a Paiboon-style tone mark:
        unmarked is mid, grave low, circumflex falling, acute high,
        caron rising (สวัสดี -> sàwàtdi).

        Returns None when the text carries no romanizable Thai
        characters (punctuation, latin script, digits) or when the
        rules do not cover its shape -- the caller then shows the word
        plain, exactly as a language without readings.
        """
        # Imported lazily: the romanizer is pure python and cheap, but
        # keeping it out of module import time means the plugin still
        # loads when the app only needs the tokenizer.
        from lute_thai_parser.romanize import (  # pylint: disable=import-outside-toplevel
            romanize,
        )

        return romanize(text)

    def get_reading_key(self, text: str):
        """
        Reading with each syllable's tone as a digit at the syllable's
        end (สวัสดี -> sa2wat2di1) -- the shape the shadowing fuzzy
        rescue splits into syllables.  None wherever get_reading is.
        """
        from lute_thai_parser.romanize import (  # pylint: disable=import-outside-toplevel
            sound_key,
        )

        return sound_key(text)
