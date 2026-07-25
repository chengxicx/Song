"""
Parsing using MeCab.

Uses natto-py (https://github.com/buruzaemon/natto-py) package and
MeCab to do parsing.

Includes classes:

- JapaneseParser

"""

from io import StringIO
import sys
import os
import re
from typing import List
from natto import MeCab
import jaconv
from lute.parse.base import ParsedToken, AbstractParser
from lute.settings.current import current_settings


class JapaneseParser(AbstractParser):
    """
    Japanese parser.

    This is only supported if mecab is installed.

    The parser uses natto-py library, and so should
    be able to find mecab automatically; if it can't,
    you may need to set the MECAB_PATH env variable,
    managed by UserSettingRepository.set_value("mecab_path", p)

    Supports two MeCab dictionary types:
    - IPADIC (default, older, widely used)
    - Unidic (newer, more accurate for modern Japanese)

    The dictionary type is detected automatically by probing
    the format of a known word, but can also be set explicitly
    via the "japanese_dict" user setting.
    """

    _is_supported = None
    _old_mecab_path = None
    _dict_type = None
    _old_dict_setting = None

    @classmethod
    def is_supported(cls):
        """
        True if a natto MeCab can be instantiated,
        otherwise false.
        """

        mecab_path = current_settings.get("mecab_path", "") or ""
        mecab_path = mecab_path.strip()

        # If the saved path doesn't exist, try to find a working one.
        if mecab_path and not os.path.exists(mecab_path):
            mecab_path = ""

        # If mecab_path is empty, check if we already auto-detected
        # a working path on a previous call.  This avoids re-running
        # the expensive MeCab() initialization on every call.
        if not mecab_path and JapaneseParser._old_mecab_path:
            # Use the previously auto-detected path.
            mecab_path = JapaneseParser._old_mecab_path

        path_unchanged = mecab_path == JapaneseParser._old_mecab_path
        if path_unchanged and JapaneseParser._is_supported is not None:
            return JapaneseParser._is_supported

        # Natto uses the MECAB_PATH env key if it's set.
        env_key = "MECAB_PATH"
        if mecab_path != "":
            os.environ[env_key] = mecab_path
        else:
            # Try to auto-detect mecab on common paths
            auto_paths = [
                "/opt/homebrew/lib/libmecab.dylib",
                "/opt/homebrew/opt/mecab/lib/libmecab.dylib",
                "/usr/local/lib/libmecab.dylib",
                "/usr/lib/libmecab.dylib",
                "/opt/homebrew/lib/libmecab.so",
                "/usr/local/lib/libmecab.so",
            ]
            for ap in auto_paths:
                if os.path.exists(ap):
                    os.environ[env_key] = ap
                    mecab_path = ap
                    break
            if mecab_path == "":
                os.environ.pop(env_key, None)

        mecab_works = False

        # Calling MeCab() prints to stderr even if the
        # exception is caught.  Suppress that output noise.
        temp_err = StringIO()
        try:
            sys.stderr = temp_err
            MeCab()
            mecab_works = True
        except:  # pylint: disable=bare-except
            mecab_works = False
        finally:
            sys.stderr = sys.__stderr__

        JapaneseParser._old_mecab_path = mecab_path
        JapaneseParser._is_supported = mecab_works

        # Reset cached dict type so it will be re-detected with
        # the (possibly changed) mecab binary.
        JapaneseParser._dict_type = None
        JapaneseParser._old_dict_setting = None

        return mecab_works

    @classmethod
    def _detect_dict_type(cls):
        """
        Detect which MeCab dictionary is currently in use.

        Strategy: parse a known word ("強い") with a custom node
        format that tries to read the Unidic "読み" field at
        feature position 8.  If that field is populated with a
        valid katakana reading, it's Unidic; otherwise we fall
        back to IPADIC.

        Returns "unidic" or "ipadic".
        """
        # Cache key: the user's dict setting + mecab path.
        dict_setting = current_settings.get("japanese_dict", "auto") or "auto"
        dict_setting = dict_setting.strip().lower()
        mecab_path = current_settings.get("mecab_path", "") or ""
        cache_key = f"{dict_setting}|{mecab_path}"

        if (
            JapaneseParser._dict_type is not None
            and JapaneseParser._old_dict_setting == cache_key
        ):
            return JapaneseParser._dict_type

        # If user set it explicitly, trust them.
        if dict_setting == "ipadic":
            JapaneseParser._dict_type = "ipadic"
            JapaneseParser._old_dict_setting = cache_key
            return "ipadic"
        if dict_setting == "unidic":
            JapaneseParser._dict_type = "unidic"
            JapaneseParser._old_dict_setting = cache_key
            return "unidic"

        # Auto-detect from the dictionary file path.
        # Unidic dictionaries are always installed under a path
        # containing "unidic" (e.g. mecab-unidic, unidic-ipadic),
        # while IPADIC paths contain "ipadic".  This is far more
        # reliable than probing feature field positions, because
        # both dictionaries have a "読み" field at position 8, so
        # %f[8] alone can't tell them apart.
        detected = "ipadic"
        try:
            with MeCab() as nm:
                for d in nm.dicts:
                    path = (d.filepath or "").lower()
                    if "unidic" in path:
                        detected = "unidic"
                        break
                    if "ipadic" in path:
                        detected = "ipadic"
                        break
        except:  # pylint: disable=bare-except
            # If anything goes wrong during detection, stay with
            # the safe default (ipadic).
            detected = "ipadic"

        JapaneseParser._dict_type = detected
        JapaneseParser._old_dict_setting = cache_key
        return detected

    @classmethod
    def name(cls):
        return "Japanese"

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        "Parse the string using MeCab."
        text = re.sub(r"[ \t]+", " ", text).strip()

        lines = []

        # If the string contains a "\n", MeCab appears to silently
        # remove it.  Splitting it works (ref test_JapaneseParser).
        # Flags: ref https://github.com/buruzaemon/natto-py:
        #    -F = node format
        #    -U = unknown format
        #    -E = EOP format
        with MeCab(r"-F %m\t%t\t%h\n -U %m\t%t\t%h\n -E EOP\t3\t7\n") as nm:
            for para in text.split("\n"):
                for n in nm.parse(para, as_nodes=True):
                    lines.append(n.feature)

        lines = [
            n.strip().split("\t") for n in lines if n is not None and n.strip() != ""
        ]

        # Production bug: JP parsing with MeCab would sometimes return a line
        # "0\t4" before an end-of-paragraph "EOP\t3\t7", reasons unknown.  These
        # "0\t4" tokens don't have any function, and cause problems in subsequent
        # steps of the processing in line_to_token(), so just remove them.
        lines = [n for n in lines if len(n) == 3]

        def line_to_token(lin):
            "Convert parsed line to a ParsedToken."
            term, node_type, third = lin
            is_eos = term in language.regexp_split_sentences
            if term == "EOP" and third == "7":
                term = "¶"

            # Node type values ref
            # https://github.com/buruzaemon/natto-py/wiki/
            #    Node-Parsing-char_type
            #
            # The repeat character is sometimes returned as a "symbol"
            # (node type = 3), so handle that specifically.
            is_word = node_type in "2678" or term == "々"
            return ParsedToken(term, is_word, is_eos or term == "¶")

        tokens = [line_to_token(lin) for lin in lines]
        return tokens

    # Hiragana is Unicode code block U+3040 - U+309F
    # ref https://stackoverflow.com/questions/72016049/
    #   how-to-check-if-text-is-japanese-hiragana-in-python
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
        if self._string_is_hiragana(text):
            return None

        jp_reading_setting = current_settings.get("japanese_reading", "").strip()
        if jp_reading_setting == "":
            # Don't set reading if nothing specified.
            return None

        dict_type = self._detect_dict_type()

        readings = []
        if dict_type == "unidic":
            # Unidic: reading (読み) is at feature field index 8.
            #
            # We use the default MeCab output format (surface TAB
            # comma-separated-features) instead of %f[8], because
            # %f[8] raises "index out of range" for symbol/unknown
            # tokens that don't have a full feature set (e.g.
            # zero-width space).  Parsing the default output is
            # more robust: we can safely check the field count and
            # fall back to the surface form for tokens without a
            # readable reading.
            with MeCab() as nm:
                raw = nm.parse(text)
            for line in raw.split("\n"):
                line = line.strip()
                if not line or line == "EOS":
                    continue
                parts = line.split("\t", 1)
                if len(parts) < 2:
                    continue
                surface = parts[0]
                features = parts[1].split(",")
                reading = features[8].strip() if len(features) > 8 else ""
                if reading and reading != "*":
                    readings.append(reading)
                else:
                    # Match IPADIC -O yomi behaviour: non-reading
                    # tokens (symbols, punctuation) pass through as
                    # their surface form.
                    readings.append(surface)
        else:
            # IPADIC: use the built-in "yomi" output format.
            flags = r"-O yomi"
            with MeCab(flags) as nm:
                for n in nm.parse(text, as_nodes=True):
                    readings.append(n.feature)
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
