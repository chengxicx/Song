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

import importlib.util
import re
import threading
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
    # Cache key for the _is_supported result: the dictionary name, the
    # only setting the support check depends on.  Kept separate from
    # the dictionary cache key below because they guard different
    # caches, and sharing one attribute made is_supported() miss its
    # cache on every call.
    _support_key = None

    # Tokenizer instances are NOT shareable: sudachipy's Tokenizer wraps
    # a mutable Rust object, and two threads tokenizing at once raise
    # "RuntimeError: Already borrowed" (measured: ~50% of calls fail
    # with 2 threads, ~92% with 12).  The Dictionary *is* shareable and
    # is the expensive part to build (~70MB of dictionary data for
    # "core"), so load exactly one and give each thread its own cheap
    # Tokenizer through thread-local storage.
    _dictionary = None
    _dictionary_key = None
    _dictionary_lock = threading.Lock()
    _thread_local = threading.local()

    # ---- support detection ----

    @classmethod
    def is_supported(cls):
        """
        True if sudachipy and a Sudachi dictionary are installed.

        Cheap on purpose.  This runs for every parser at app start-up
        (lute.parse.registry.supported_parsers), so it must not build
        the dictionary: that costs ~33MB of private memory plus a read
        of the 200MB+ system.dic, all wasted on users with no Japanese
        books.  The dictionary is built by the first real parse instead
        (see _build_tokenizer).
        """
        dict_type = cls._get_dict_setting()

        if (
            JapaneseSudachiParser._is_supported is not None
            and JapaneseSudachiParser._support_key == dict_type
        ):
            return JapaneseSudachiParser._is_supported

        JapaneseSudachiParser._is_supported = cls._dictionary_is_installed(dict_type)
        JapaneseSudachiParser._support_key = dict_type
        return JapaneseSudachiParser._is_supported

    @staticmethod
    def _dictionary_is_installed(dict_type: str) -> bool:
        """
        True if sudachipy and one of the sudachidict packages can be
        imported.  Any of the three will do: _load_dictionary falls
        back to whichever one is installed.
        """
        if importlib.util.find_spec("sudachipy") is None:
            return False

        names = ["sudachidict_core", "sudachidict_small", "sudachidict_full"]
        names.insert(0, f"sudachidict_{dict_type}")
        return any(importlib.util.find_spec(n) is not None for n in names)

    @classmethod
    def _invalidate_cache(cls):
        """
        Drop the cached support flag and shared dictionary, so the next
        call reloads them (e.g. after a database restore or a change of
        the sudachi dictionary setting).  Per-thread tokenizers are
        keyed by dictionary name, so they rebuild themselves lazily.
        """
        JapaneseSudachiParser._is_supported = None
        JapaneseSudachiParser._support_key = None
        JapaneseSudachiParser._dictionary = None
        JapaneseSudachiParser._dictionary_key = None

    # ---- settings helpers ----

    @classmethod
    def _get_dict_setting(cls) -> str:
        v = current_settings().get("japanese_sudachi_dict", "core") or "core"
        v = v.strip().lower()
        if v not in ("small", "core", "full"):
            v = "core"
        return v

    @classmethod
    def _get_mode_setting(cls) -> str:
        v = current_settings().get("japanese_sudachi_mode", "C") or "C"
        v = v.strip().upper()
        if v not in ("A", "B", "C"):
            v = "C"
        return v

    # ---- tokenizer construction (cached) ----

    @classmethod
    def _build_tokenizer(cls, dict_type: str):
        """
        Return this thread's SudachiPy tokenizer for the given
        dictionary type, creating it on first use.

        One shared Dictionary, one cheap Tokenizer per thread: the
        Tokenizer cannot be used from two threads at once, the
        Dictionary can be shared.
        """
        tls = JapaneseSudachiParser._thread_local
        tok = getattr(tls, "tokenizer", None)
        if tok is not None and getattr(tls, "tokenizer_key", None) == dict_type:
            return tok

        sd = cls._get_dictionary(dict_type)
        tok = sd.create()
        tls.tokenizer = tok
        tls.tokenizer_key = dict_type
        return tok

    @classmethod
    def _get_dictionary(cls, dict_type: str):
        """
        Build (or return cached) SudachiPy Dictionary for the given
        dictionary type.  Guarded by a lock: loading a dictionary is
        expensive and must not happen once per thread.
        """
        with JapaneseSudachiParser._dictionary_lock:
            if (
                JapaneseSudachiParser._dictionary is not None
                and JapaneseSudachiParser._dictionary_key == dict_type
            ):
                return JapaneseSudachiParser._dictionary

            sd = cls._load_dictionary(dict_type)
            JapaneseSudachiParser._dictionary = sd
            JapaneseSudachiParser._dictionary_key = dict_type
            return sd

    @classmethod
    def _load_dictionary(cls, dict_type: str):
        """
        Load a SudachiPy Dictionary, tolerating the API change from
        Dictionary(dict_type=...) (<=0.6.x) to Dictionary(dict=...)
        (>=0.7.x), and finally falling back to the default dictionary.
        """
        import warnings  # pylint: disable=import-outside-toplevel
        from sudachipy import Dictionary  # pylint: disable=import-outside-toplevel

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            try:
                return Dictionary(dict=dict_type)
            except TypeError:
                try:
                    return Dictionary(dict_type=dict_type)
                except Exception:  # pylint: disable=broad-except
                    return Dictionary()
            except Exception:  # pylint: disable=broad-except
                return Dictionary()

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

        jp_reading_setting = current_settings().get("japanese_reading", "").strip()
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

    def _reading_from_kana(self, surface: str, kana: str, setting: str):
        """
        One morpheme's yomi (katakana) as the display reading under the
        japanese_reading setting; None when it adds nothing over the
        surface (symbols, kanji the dictionary has no kana for).
        """
        if not kana or kana == surface:
            # Kana read as themselves: the surface is the reading.
            return self._self_reading(surface, setting)
        if setting == "katakana":
            ret = kana
        elif setting == "hiragana":
            ret = jaconv.kata2hira(kana)
        elif setting == "alphabet":
            ret = jaconv.kata2alphabet(kana)
        else:
            raise RuntimeError(f"Bad reading type {setting}")
        # A particle or okurigana morpheme's yomi converts back to the
        # surface itself (の <- ノ).
        if ret == surface:
            return self._self_reading(surface, setting)
        return ret

    @staticmethod
    def _string_has_kanji(s: str) -> bool:
        "True if any character is a kanji (incl. the 々 iteration mark)."
        return any("\u4E00" <= c <= "\u9FFF" or c == "\u3005" for c in s)

    def _neighbour_window_kana(self, tok, morphs, index, split_mode):
        """
        The yomi this morpheme gets when read together with its immediate
        neighbours, or None when that window cannot be trusted.

        Reading the whole sentence at once is what makes 一つ -> 一=ヒト
        work, but the further a morpheme sits from the start of a long
        string the more its reading can drift: 数ある with 一つ one
        morpheme later comes back 数=スウ, while those same two morphemes
        read on their own give the correct カズ.  A window of the
        morpheme plus its neighbours keeps the local context that
        disambiguates without the distance that drifts.

        The left neighbour is kept when there is one because it is often
        what fixes the reading -- 杯 is バイ in 一杯 but サカズキ alone,
        日 is ニチ in 一日 but ヒ alone -- so a window that dropped it
        would break exactly those.  It is allowed to be absent at the
        start of the text, where there is no left context to preserve.
        The right neighbour is required: it is the side that does the
        disambiguating, and a window that stops at the end of the text
        knows no more than the morpheme on its own.

        Returns None unless the window tokenizes back to a morpheme with
        exactly this surface at exactly this offset, so a window that
        merges or splits differently leaves the sentence's own reading
        standing and nothing is guessed.
        """
        if index + 1 >= len(morphs):
            return None
        start = max(0, index - 1)
        window = morphs[start : index + 2]
        surface = morphs[index][0]
        left_text = "".join(s for s, _ in window[: index - start])
        try:
            got = list(tok.tokenize("".join(s for s, _ in window), mode=split_mode))
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        seen = ""
        for m in got:
            if seen == left_text and m.surface() == surface:
                reading = m.reading_form()
                return reading if reading and reading != "*" else None
            seen += m.surface()
        return None

    def get_context_readings(self, text: str):
        """
        Per-morpheme readings from one contextual tokenize of `text`.

        Returns [(surface, reading-or-None), ...]: each morpheme read as
        the surrounding sentence disambiguates it (一つ -> 一=ヒト), not
        as the isolated surface would be re-read (一 -> イチ).  Applies
        the same japanese_reading setting as get_reading; None when that
        setting is unset.

        A kanji morpheme's reading is taken from the window it forms with
        its immediate neighbours rather than from the whole sentence,
        which is what keeps 数ある at カズ; see _neighbour_window_kana.
        """
        zws = "\u200B"
        text = text.replace(zws, "")

        if self._string_is_hiragana(text):
            return None

        jp_reading_setting = current_settings().get("japanese_reading", "").strip()
        if jp_reading_setting == "":
            return None

        dict_type = self._get_dict_setting()
        mode = self._get_mode_setting()
        tok = self._build_tokenizer(dict_type)
        split_mode = self._get_split_mode(mode)

        morphs = [
            (m.surface(), m.reading_form())
            for m in tok.tokenize(text, mode=split_mode)
            if m.surface()
        ]

        out = []
        for i, (surface, reading) in enumerate(morphs):
            kana = reading if reading and reading != "*" else surface
            if self._string_has_kanji(surface):
                # Only a kanji morpheme can be mis-read; kana read as
                # themselves, so the extra window parse would be wasted.
                window_kana = self._neighbour_window_kana(tok, morphs, i, split_mode)
                if window_kana:
                    kana = window_kana
            out.append(
                (surface, self._reading_from_kana(surface, kana, jp_reading_setting))
            )
        return out or None

    @staticmethod
    def _string_is_kana(s: str) -> bool:
        "True if every character is hiragana or katakana (incl. ー)."
        return bool(s) and all("\u3040" <= c <= "\u30FF" for c in s)

    @classmethod
    def _self_reading(cls, surface: str, setting: str):
        """
        A kana morpheme's reading under the setting; None for other
        surfaces (symbols, kanji the dictionary has no kana for).
        """
        if not cls._string_is_kana(surface):
            return None
        if setting == "katakana":
            return jaconv.hira2kata(surface)
        if setting == "hiragana":
            return surface
        if setting == "alphabet":
            return jaconv.kata2alphabet(surface)
        raise RuntimeError(f"Bad reading type {setting}")

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

    def content_token_count(self, text: str) -> int:
        """
        Number of content-word tokens (自立語) in the given text.

        Used by the lemma/parent backfill (roadmap 2.3, see
        lute/term/lemma_parents.py): a term that is a concatenation of
        several content words (似ている) resolves to a concatenated
        "lemma" (似るいる) which must not become a parent term, whereas
        a single content word plus auxiliaries (食べた -> 食べる + た)
        resolves to a real dictionary form.

        Deliberately a separate walk rather than a refactor of
        get_lemma(): get_lemma() runs on the reading page and in
        find_or_new(), and is pinned by the parser and grammar tests.
        """
        zws = "\u200B"
        text = text.replace(zws, "")

        if self._string_is_hiragana(text):
            return 0

        tok = self._build_tokenizer(self._get_dict_setting())
        split_mode = self._get_split_mode(self._get_mode_setting())

        count = 0
        for m in tok.tokenize(text, mode=split_mode):
            if self._is_content_token(m.part_of_speech(), m.surface()):
                count += 1
        return count
