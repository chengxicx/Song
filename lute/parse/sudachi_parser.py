"""
Parsing using SudachiPy.

Uses sudachipy (https://github.com/WorksApplications/sudachi.rs) package
to do Japanese morphological analysis.

Includes classes:

- JapaneseSudachiParser

SudachiPy offers:
- Multiple split modes (A=shortest, B=middle, C=longest)
- Multiple dictionary sizes (small, core, full)
- Reading (kana) and lemma (dictionary form) extraction

This parser is independent of the MeCab-based JapaneseParser.
Users select it as the "Parse as" type for their Japanese language.
"""

import re
from typing import List

import jaconv

from lute.parse.base import ParsedToken, AbstractParser
from lute.settings.current import current_settings


class JapaneseSudachiParser(AbstractParser):
    """
    Japanese parser using SudachiPy.

    This is only supported if sudachipy and at least one Sudachi
    dictionary (sudachidict_small / sudachidict_core / sudachidict_full)
    are installed.

    Configuration via UserSettings:
      - japanese_sudachi_dict: "small" | "core" | "full" (default "core")
      - japanese_sudachi_mode: "A" | "B" | "C"  (default "C")
      - japanese_reading: "" | "katakana" | "hiragana" | "alphabet"
        (shared with the MeCab parser; empty = no reading)
    """

    _is_supported = None
    _instance = None
    _instance_key = None

    # ---- support detection ----

    @classmethod
    def is_supported(cls):
        """
        True if sudachipy can be imported and a dictionary loaded.
        """
        dict_type = cls._get_dict_setting()
        mode = cls._get_mode_setting()
        cache_key = f"{dict_type}|{mode}"

        if (
            JapaneseSudachiParser._is_supported is not None
            and JapaneseSudachiParser._instance_key == cache_key
        ):
            return JapaneseSudachiParser._is_supported

        try:
            cls._build_tokenizer(dict_type)
            JapaneseSudachiParser._is_supported = True
        except Exception:  # pylint: disable=broad-except
            JapaneseSudachiParser._is_supported = False

        JapaneseSudachiParser._instance_key = cache_key
        return JapaneseSudachiParser._is_supported

    # ---- settings helpers ----

    @classmethod
    def _get_dict_setting(cls) -> str:
        v = current_settings.get("japanese_sudachi_dict", "core") or "core"
        v = v.strip().lower()
        if v not in ("small", "core", "full"):
            v = "core"
        return v

    @classmethod
    def _get_mode_setting(cls) -> str:
        v = current_settings.get("japanese_sudachi_mode", "C") or "C"
        v = v.strip().upper()
        if v not in ("A", "B", "C"):
            v = "C"
        return v

    # ---- tokenizer construction (cached) ----

    @classmethod
    def _build_tokenizer(cls, dict_type: str):
        """
        Build (or return cached) SudachiPy tokenizer for the given
        dictionary type.
        """
        import warnings  # pylint: disable=import-outside-toplevel
        from sudachipy import Dictionary  # pylint: disable=import-outside-toplevel

        cache_key = dict_type
        if (
            JapaneseSudachiParser._instance is not None
            and JapaneseSudachiParser._instance_key == cache_key
        ):
            return JapaneseSudachiParser._instance

        # Dictionary() auto-discovers installed sudachidict packages.
        # The parameter name changed from "dict_type" (<=0.6.x) to
        # "dict" (>=0.7.x).  Try the new name first, fall back to the
        # old one, then to no parameter (uses default "core").
        tok = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                sd = Dictionary(dict=dict_type)
                tok = sd.create()
            except TypeError:
                try:
                    sd = Dictionary(dict_type=dict_type)
                    tok = sd.create()
                except Exception:
                    sd = Dictionary()
                    tok = sd.create()
            except Exception:
                sd = Dictionary()
                tok = sd.create()

        JapaneseSudachiParser._instance = tok
        JapaneseSudachiParser._instance_key = cache_key
        return tok

    @classmethod
    def _get_split_mode(cls, mode: str):
        from sudachipy import (  # pylint: disable=import-outside-toplevel
            SplitMode,
        )

        return {
            "A": SplitMode.A,
            "B": SplitMode.B,
            "C": SplitMode.C,
        }.get(mode, SplitMode.C)

    @classmethod
    def name(cls):
        return "Japanese (Sudachi)"

    @classmethod
    def languages(cls):
        return {"japanese"}

    # ---- parsing ----

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        "Parse the string using SudachiPy."
        text = re.sub(r"[ \t]+", " ", text).strip()

        dict_type = self._get_dict_setting()
        mode = self._get_mode_setting()
        tok = self._build_tokenizer(dict_type)
        split_mode = self._get_split_mode(mode)

        tokens = []
        for para in text.split("\n"):
            result = tok.tokenize(para, mode=split_mode)
            prev_end = 0
            for m in result:
                surface = m.surface()
                if surface == "":
                    continue
                # Insert any gap text (spaces, punctuation that Sudachi
                # didn't return as a morpheme) between the previous
                # token and this one as a non-word token, so that
                # word spacing is preserved in the rendered text.
                m_begin = m.begin()
                if m_begin > prev_end:
                    gap = para[prev_end:m_begin]
                    if gap:
                        tokens.append(ParsedToken(gap, False, False))
                pos = m.part_of_speech()
                is_word = self._is_selectable_token(pos, surface)
                is_eos = surface in language.regexp_split_sentences
                tokens.append(ParsedToken(surface, is_word, is_eos))
                prev_end = m.end()
            # Trailing gap.
            if prev_end < len(para):
                gap = para[prev_end:]
                if gap:
                    tokens.append(ParsedToken(gap, False, False))
            # End-of-paragraph sentinel.
            tokens.append(ParsedToken("¶", False, True))

        return tokens

    # ---- POS helpers ----

    # Sudachi POS tuple is (pos1, pos2, pos3, pos4, pos5).
    # pos1 values: 名詞, 動詞, 形容詞, 形状詞, 副詞, 連体詞, 接続詞,
    #              感動詞, 助動詞, 助詞, 補助記号, 記号, 接頭辞, 接尾辞, ...
    #
    # Two different "word" notions are used:
    #
    # - _BOUND_POS1: bound grammatical morphemes whose *lemmas* must be
    #   skipped during get_lemma(), otherwise conjugated forms resolve to
    #   broken parents like 来るた or 置くて.  This is only a lemma
    #   filter -- it does NOT make the tokens unclickable.
    #
    # - _SYMBOL_POS1: pos1 categories that are punctuation/symbols and
    #   should not be independently selectable on the reading page.
    _BOUND_POS1 = {"助詞", "助動詞", "記号", "補助記号", "接尾辞", "接頭辞"}
    _SYMBOL_POS1 = {"記号", "補助記号"}

    def _is_content_token(self, pos: tuple, surface: str) -> bool:
        """
        True if the token is a content word whose lemma should be kept
        when building a parent/lemma form.

        Particles (助詞) and auxiliary verbs (助動詞) are excluded so
        that conjugated forms don't resolve to broken parents (e.g.
        来た -> 来るた, 置いて -> 置くて).
        """
        if not pos:
            return False
        pos1 = pos[0] if len(pos) > 0 else ""
        if pos1 in self._BOUND_POS1:
            return False
        # Whitespace / blank tokens.
        if surface.strip() == "":
            return False
        return True

    def _is_selectable_token(self, pos: tuple, surface: str) -> bool:
        """
        True if the token should be clickable/selectable on the reading
        page (i.e. rendered with the `word` class so a popup opens).

        Only punctuation/symbols (記号/補助記号) and blank tokens are
        non-selectable.  Particles, auxiliary verbs, prefixes and
        suffixes remain selectable so learners can look them up -- this
        matches the MeCab parser, which makes hiragana morphemes
        clickable (e.g. 溺れてく's て・く, or って in 平気だよって).
        """
        if not pos:
            return False
        pos1 = pos[0] if len(pos) > 0 else ""
        if pos1 in self._SYMBOL_POS1:
            return False
        # Whitespace / blank tokens.
        if surface.strip() == "":
            return False
        return True

    # ---- reading ----

    # Hiragana is Unicode code block U+3040 - U+309F
    def _char_is_hiragana(self, c) -> bool:
        return "\u3040" <= c <= "\u309F"

    def _string_is_hiragana(self, s: str) -> bool:
        return all(self._char_is_hiragana(c) for c in s)

    def get_reading(self, text: str):
        """
        Get the pronunciation for the given text.

        Returns None if the text is all hiragana, or the pronunciation
        doesn't add value (same as text).
        """
        zws = "\u200B"
        text = text.replace(zws, "")

        if self._string_is_hiragana(text):
            return None

        jp_reading_setting = current_settings.get("japanese_reading", "").strip()
        if jp_reading_setting == "":
            return None

        dict_type = self._get_dict_setting()
        mode = self._get_mode_setting()
        tok = self._build_tokenizer(dict_type)
        split_mode = self._get_split_mode(mode)

        result = tok.tokenize(text, mode=split_mode)
        readings = []
        for m in result:
            surface = m.surface()
            if surface == "":
                continue
            reading = m.reading_form()
            if reading and reading != "*":
                readings.append(reading)
            else:
                # Pass through surface for tokens without reading
                # (symbols, punctuation).
                readings.append(surface)

        readings = [r.strip() for r in readings if r is not None and r.strip() != ""]
        ret = "".join(readings).strip()
        if ret in ("", text):
            return None

        if jp_reading_setting == "katakana":
            return ret
        if jp_reading_setting == "hiragana":
            return jaconv.kata2hira(ret)
        if jp_reading_setting == "alphabet":
            return jaconv.kata2alphabet(ret)
        raise RuntimeError(f"Bad reading type {jp_reading_setting}")

    # ---- lemma ----

    def get_lemma(self, text: str):
        """
        Get the dictionary/lemma form of the given text.

        Uses Sudachi's dictionary_form().  Only lemmas from content
        words (自立語) are used; 助詞 and 助動詞 suffix lemmas are
        skipped so that conjugated forms resolve cleanly.
        """
        zws = "\u200B"
        text = text.replace(zws, "")

        if self._string_is_hiragana(text):
            return None

        dict_type = self._get_dict_setting()
        mode = self._get_mode_setting()
        tok = self._build_tokenizer(dict_type)
        split_mode = self._get_split_mode(mode)

        result = tok.tokenize(text, mode=split_mode)
        lemmas = []
        for m in result:
            surface = m.surface()
            if surface == "":
                continue
            pos = m.part_of_speech()
            if not self._is_content_token(pos, surface):
                continue
            lemma = m.dictionary_form()
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
