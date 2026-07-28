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

        tokens = []

        # Use node attributes directly (surface, char_type, posid)
        # instead of a custom format string, because the format
        # string fields (%t, %h) behave differently between IPADIC
        # and Unidic -- Unidic %h expands to the full feature string
        # rather than a single numeric ID, which breaks tab parsing.
        #
        # Node attribute reference:
        #   n.surface  = surface form (表層系)
        #   n.char_type = character type (文字種別, 2=kanji etc.)
        #   n.stat     = node status (0=normal, 1=unknown, 2=BOS/EOS)
        #   n.posid    = part-of-speech id
        #
        # We still need an EOP (end-of-paragraph) sentinel, so we
        # append a fake EOP token after each paragraph.
        with MeCab() as nm:
            for para in text.split("\n"):
                for n in nm.parse(para, as_nodes=True):
                    # Skip BOS/EOS nodes
                    if n.stat == 2 or n.surface is None or n.surface == "":
                        continue
                    term = n.surface
                    node_type = str(n.char_type)
                    is_eos = term in language.regexp_split_sentences
                    # Node type values ref
                    # https://github.com/buruzaemon/natto-py/wiki/
                    #    Node-Parsing-char_type
                    #
                    # The repeat character is sometimes returned as a
                    # "symbol" (node type = 3), so handle that
                    # specifically.
                    is_word = node_type in "2678" or term == "々"
                    tokens.append(ParsedToken(term, is_word, is_eos))
                # End-of-paragraph sentinel, matches the old
                # "EOP\t3\t7" format where third==7 marks it.
                tokens.append(ParsedToken("¶", False, True))

        return tokens

    # Hiragana is Unicode code block U+3040 - U+309F
    # ref https://stackoverflow.com/questions/72016049/
    #   how-to-check-if-text-is-japanese-hiragana-in-python
    def _char_is_hiragana(self, c) -> bool:
        return "\u3040" <= c <= "\u309F"

    def _string_is_hiragana(self, s: str) -> bool:
        return all(self._char_is_hiragana(c) for c in s)

    def _find_unidic_kana_index(self, features: List[str]) -> int:
        """
        Find the best candidate field index for the surface-form kana
        reading (仮名形出現形) in a Unidic feature vector.

        Different Unidic releases place the kana field at different
        positions:
          - Unidic 2.x (older, bundled with some mecab packages) → index 9
          - Unidic 3.x / unidic-cwj v102+ (pip `unidic` package)  → indices 10-14

        Instead of hard-coding a single position we scan the candidate
        range (9..14) and pick the first entry that looks like a
        valid surface reading: a non-empty, non-asterisk katakana
        string.  If nothing matches, -1 is returned and the caller
        falls back to the surface form.
        """
        # Field 6 always exists but is the *lemma* reading (語彙素読み),
        # which returns the dictionary-form pronunciation -- not the
        # conjugated surface reading we want.  Start scanning from 9.
        candidate_range = range(9, min(15, len(features)))
        katakana_re = re.compile(r"^[\u30A0-\u30FF\u30FC0-9]+$")
        for idx in candidate_range:
            val = features[idx].strip()
            if not val or val == "*":
                continue
            # Surface kana readings must be pure katakana (plus
            # long-vowel marks ー and occasional digits for loanwords).
            if katakana_re.match(val):
                return idx
        return -1

    def get_reading(self, text: str):
        """
        Get the pronunciation for the given text.

        Returns None if the text is all hiragana, or the pronunciation
        doesn't add value (same as text).
        """
        # Strip zero-width spaces (zws) that mark token boundaries in
        # multiword terms. MeCab treats zws as a separate token, which
        # would pollute the reading with spurious characters and
        # break the "same as text" check below.
        zws = "\u200B"
        text = text.replace(zws, "")

        if self._string_is_hiragana(text):
            return None

        jp_reading_setting = current_settings.get("japanese_reading", "").strip()
        if jp_reading_setting == "":
            # Don't set reading if nothing specified.
            return None

        dict_type = self._detect_dict_type()

        readings = []
        if dict_type == "unidic":
            # Unidic: the reading of the surface form (i.e. how the
            # token is actually pronounced in its conjugated form,
            # equivalent to IPADIC's "読み" / -O yomi output) lives at
            # different indices depending on the Unidic release.
            # We scan a candidate range for each token rather than
            # hard-coding a single index, which keeps this working
            # across Unidic 2.x, 3.x, and the v102 release used on
            # the server deploy.
            #
            # We use the default MeCab output format (surface TAB
            # comma-separated-features) instead of %f[N], because
            # %f[N] raises "index out of range" for symbol/unknown
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
                kana_idx = self._find_unidic_kana_index(features)
                reading = features[kana_idx].strip() if kana_idx >= 0 else ""
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

    # Standardize unidic-specific lemma orthographies to the common
    # dictionary forms users expect.  Unidic's 語彙素 field occasionally
    # uses historical/hybridgaki (変換漢字) forms that IPADIC never uses.
    _UNIDIC_LEMMA_NORMALIZATION_MAP = {
        "為る": "する",
        "為": "す",
        "居る": "いる",
        "来る": "来る",  # keep as-is (also common orthography)
        "為さ": "し",    # 為さ (mizen) -> する stem
        "為れ": "す",    # 為れ (izen) -> する stem
    }

    # POS (and sub-POS) categories that mark a token as a *bound*
    # grammatical morpheme.  Any token whose top-level or mid-level
    # POS contains one of these is skipped during lemma construction.
    #
    # We check not only features[0] (top POS) but also features[1..3]
    # because Unidic 3.x/v102 sometimes nests "bound" classifications
    # one level deeper (e.g. 名詞-接尾 where the top-level is 名詞 but
    # the word is still a purely grammatical suffix like さ, み, etc.).
    _FUSHI_POS_HINTS = (
        "助詞",
        "助動詞",
        "接尾",
        "接頭",
        "助数詞",
        "準助詞",
        "終助詞",
        "接続助詞",
        "格助詞",
        "副助詞",
        "連体詞",  # standalone 連体詞 is jiritsugo but rare; keep if top-level only
        # NOTE: 連体詞 intentionally excluded from this tuple because
        # it can be an independent word like この・その -- we only skip
        # it when it appears as a *sub*-category of something else.
    )

    def _normalize_unidic_lemma(self, lemma: str) -> str:
        """Normalize a unidic lemma to common written forms."""
        # First, pass through the fixed orthography map.
        lemma = self._UNIDIC_LEMMA_NORMALIZATION_MAP.get(lemma, lemma)
        return lemma

    def _clean_lemma_value(self, lemma: str) -> str:
        """
        Strip classifier / descriptive junk that some Unidic variants
        attach to the 語彙素 field with dash or space separators.

        Examples that become clean lemmas:
          "差す - 他動詞"    -> "差す"
          "食べる-一段"    -> "食べる"
          "する 動詞-サ変" -> "する"

        POS labels can start with kanji (他動詞, 一段, 五段, etc.) so
        we can't key off character class alone.  Instead we split at
        the first POS-style separator which can be any of:
          - " - "  (space-dash-space conventional POS label separator)
          - any run of whitespace followed by more text
          - a bare ASCII '-' immediately followed by a NON long-vowel
            character (to avoid chopping legitimate 長音 in katakana).
        """
        if not lemma:
            return lemma
        # Pattern priority (via alternation order): " - " > " " > "-"
        # The negative lookahead (?![ー]) on the last branch ensures we
        # never mistake a 長音 ー (U+30FC) for the ASCII-hyphen POS
        # separator (コーヒー stays intact).
        parts = re.split(r"\s+-\s+|\s+|-(?=[^ー])", lemma, maxsplit=1)
        return parts[0].strip() if parts else lemma

    def _is_jiritsugo(self, features: List[str]) -> bool:
        """
        True if the feature list describes an independent word (自立語).

        We only want lemmas from independent words (verbs, adjectives,
        nouns, etc.).  Auxiliary verbs (助動詞) and particles (助詞) are
        purely grammatical suffix tokens whose lemmas must NOT be
        concatenated onto the parent, otherwise we get broken parents
        like 来るた (for 来た), 置くて (for 置いて), 降るて居るて
        (for 降っていて), or 差す-他動詞ますた (for 指しました).
        """
        if not features:
            return False

        top_pos = (features[0] or "").strip()

        # --- Quick-reject: top-level bound POS ---
        # Top-level 記号, 補助記号, 空白, etc. are never content words.
        if top_pos in ("記号", "補助記号", "空白", "補助記号"):
            return False

        # Top-level 助詞 / 助動詞: always skip.  These account for the
        # majority of the 「来るた / 置くて / 降って居るて」 suffix bugs.
        if top_pos in ("助詞", "助動詞"):
            return False

        # --- Sub-POS check (inspect finer classifications) ---
        # Unidic nests things like 「名詞-接尾-サ変接続」 (a bound suffix
        # like さ doing nominalization) or 「動詞-非自立可能」
        # (auxiliary 居る / 為る bound to a stem) deeper in the feature
        # vector.  Treat any of these "bound" hints as non-independent.
        sub_positions = [f.strip() for f in features[1:4] if f]
        # A few "sub" tags *always* mean the word is a bound morpheme
        # regardless of top-level POS.
        strong_bound_hints = {
            "助詞", "助動詞", "接尾", "助数詞", "接続助詞",
            "格助詞", "副助詞", "終助詞", "準助詞",
            "非自立", "非自立可能", "準助動詞",
        }
        for sub in sub_positions:
            if sub in strong_bound_hints:
                return False

        # Top-level 連体詞 (この/その/あんな) is borderline -- keep it
        # (it passes the above) as it behaves like an independent word
        # for user lookup purposes.

        # Anything that survived the above filters and has a non-empty
        # top POS is treated as jiritsugo.  This includes:
        #   動詞, 形容詞, 形容動詞, 名詞, 副詞, 連体詞, 感動詞, etc.
        return top_pos != ""

    def get_lemma(self, text: str):
        """
        Get the dictionary/lemma form of the given text.

        For Japanese, returns the basic form (辞書形) of verbs,
        adjectives, and other inflected words.  Returns None if
        the text is already in its base form or can't be determined.

        Only lemmas from 自立語 (independent words) are used; 助詞
        and 助動詞 suffix lemmas are deliberately skipped so that a
        conjugated form like 来た resolves to 来る (not 来るた).
        """
        # Strip zero-width spaces (zws) that mark token boundaries in
        # multiword terms. MeCab treats zws as a separate token, which
        # would pollute the lemma with spurious characters and produce
        # incorrect parent terms.
        zws = "\u200B"
        text = text.replace(zws, "")

        if self._string_is_hiragana(text):
            return None

        dict_type = self._detect_dict_type()

        # Lemma field position differs between dictionaries:
        #   IPADIC: index 6 (基本形)
        #   Unidic: index 7 (語彙素 / lemma)
        lemma_index = 7 if dict_type == "unidic" else 6

        lemmas = []
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
            # --- CRITICAL FIX ---
            # Skip auxiliary verbs, particles, and bound morphemes
            # entirely.  Their "lemmas" (た, て, です, ます, 居る, etc.)
            # are purely grammatical and must not be concatenated onto
            # the content-word base form.
            if not self._is_jiritsugo(features):
                continue
            lemma = features[lemma_index].strip() if len(features) > lemma_index else ""
            if lemma and lemma != "*":
                # Unidic variants (esp. v102) occasionally stuff POS
                # classifier junk onto the 語彙素 value with dashes or
                # spaces (e.g. "差す - 他動詞").  Strip that before use.
                if dict_type == "unidic":
                    lemma = self._clean_lemma_value(lemma)
                    lemma = self._normalize_unidic_lemma(lemma)
                if lemma:
                    lemmas.append(lemma)
                    continue
            lemmas.append(surface)

        if not lemmas:
            return None

        ret = "".join(lemmas).strip()
        if ret in ("", text):
            return None
        return ret
