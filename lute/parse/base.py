"""
Common classes use for all parsing.
"""

from abc import ABC, abstractmethod
from typing import List


class ParsedToken:
    """
    A single parsed token from an input text.

    As tokens are created, the class counters
    (starting with cls_) are assigned to the ParsedToken
    and then incremented appropriately.
    """

    # Class counters.
    cls_sentence_number = 0
    cls_order = 0

    @classmethod
    def reset_counters(cls):
        """
        Reset all the counters.
        """
        ParsedToken.cls_sentence_number = 0
        ParsedToken.cls_order = 0

    def __init__(
        self, token: str, is_word: bool, is_end_of_sentence: bool = False, pos=None
    ):
        self.token = token
        self.is_word = is_word
        self.is_end_of_sentence = is_end_of_sentence

        # The parser's part-of-speech tuple for this token, or None when
        # the parser has no POS to offer (MeCab exposes only a numeric
        # id).  It is carried on the token so that a term created from
        # an already-parsed page can be tagged later without re-parsing
        # the word on its own -- which would give a different answer,
        # because POS is context sensitive (出そう is 五段-サ行 /
        # 意志推量形 in 「声を出そうとしましたが」 but 下一段-ダ行 /
        # 連用形-一般 on its own).
        self.pos = pos

        ParsedToken.cls_order += 1
        self.order = ParsedToken.cls_order

        self.sentence_number = ParsedToken.cls_sentence_number

        # Increment counters after the TextToken has been
        # completed, so that it belongs to the correct
        # sentence.
        if self.is_end_of_sentence:
            ParsedToken.cls_sentence_number += 1

    @property
    def is_end_of_paragraph(self):
        return self.token.strip() == "¶"

    def __repr__(self):
        attrs = [
            f"word: {self.is_word}",
            f"eos: {self.is_end_of_sentence}",
            # f"sent: {self.sentence_number}",
        ]
        attrs = ", ".join(attrs)
        return f'<"{self.token}" ({attrs})>'


def character_substitution_pairs(language):
    """
    Parse `language.character_substitutions` ("´='|’='|...=…") into
    [(from, to), ...].

    This is the list a parser applies before tokenizing, so that a curved
    apostrophe in the book and a straight one in the dictionary are the
    same word.  It is shared here because every parser that uses the
    setting must report the same pairs through text_substitutions().
    """
    pairs = []
    for replacement in (language.character_substitutions or "").split("|"):
        fromto = replacement.strip().split("=")
        if len(fromto) >= 2:
            pairs.append((fromto[0].strip(), fromto[1].strip()))
    return pairs


class AbstractParser(ABC):
    """
    Abstract parser, inherited from by all parsers.

    Attributes:
        data_directory: Optional.  A full path to a
            directory that the parser uses.  Should be
            initialized with init_data_directory().
    """

    data_directory = None

    @classmethod
    def uses_data_directory(cls):
        "True if the parser needs user-supplied data."
        return False

    @classmethod
    def init_data_directory(cls):
        """
        Initialize the data_directory if needed.  Not
        necessary for all parsers.
        """
        return

    @classmethod
    def is_supported(cls):
        """
        True if the parser will work on the current system.

        Some parsers, such as Japanese, require external
        components to be present and configured.  If missing,
        this should return False.
        """
        return True

    @classmethod
    @abstractmethod
    def name(cls):
        """
        Parser name, for displaying in UI.
        """

    @classmethod
    def languages(cls):
        """
        Return a set of language names this parser is designed for, or
        None if the parser works for any language (generic).

        Used to filter the "Parse as" dropdown in the language edit
        form so that e.g. a Japanese language only offers Japanese
        parsers (MeCab / Sudachi), not Turkish or other languages.
        """
        return None

    @abstractmethod
    def get_parsed_tokens(self, text: str, language) -> List:
        """
        Get an array of ParsedTokens from the input text for the given language.
        """

    def text_substitutions(self, language):  # pylint: disable=unused-argument
        """
        Literal replacements applied to the text before it is tokenized,
        in the order they are applied.

        The token stream is what the reader renders, so for most books
        this never surfaces: the tokens are the text.  Reader-mode EPUB
        renders the chapter's own HTML instead, and the two only line up
        if the chapter's text nodes have had the same replacements
        applied -- so the parser has to be able to say what it did.
        Returns [(from, to), ...], empty for a parser that tokenizes the
        text as given.
        """
        return []

    def get_reading(self, text: str):  # pylint: disable=unused-argument
        """
        Get the pronunciation for the given text.  For most
        languages, this can't be automated.
        """
        return None

    def get_context_readings(self, text: str):  # pylint: disable=unused-argument
        """
        Per-morpheme readings from a single parse of the whole text.

        Returns [(surface, reading-or-None), ...] in morpheme order, so
        callers can attach readings that respect the surrounding context
        (e.g. 一つ read as ひとつ, not いち + つ).  None when the parser
        does not support it.
        """
        return None

    def get_lemma(self, text: str):  # pylint: disable=unused-argument
        """
        Get the dictionary/lemma form of the given text.
        For most languages, this returns the text as-is.
        """
        return None

    def get_pos_tag(self, pos):  # pylint: disable=unused-argument
        """
        The term tag to derive from a parsed token's POS, or None.

        None means "this parser has no opinion", and the term simply
        never gets an automatic tag: most languages have no POS here,
        and MeCab exposes only a numeric id.  Parsers that can answer
        also decide what is worth tagging -- see
        JapaneseSudachiParser.get_pos_tag, which skips particles,
        auxiliaries and affixes.
        """
        return None

    def get_lowercase(self, text: str):
        """
        Return the lowcase text.

        Most languages can use the built-in lowercase operation,
        but some (like Turkish) need special handling.
        """
        return text.lower()
