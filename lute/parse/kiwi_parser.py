"""
Parsing for Korean using Kiwi (kiwipiepy).

Uses kiwipiepy (https://github.com/bab2min/kiwipiepy) package,
a fast Korean morphological analyzer based on the Kiwi C++ library.

Includes classes:

- KoreanParser

Configurable behaviour via columns on the Language model (only used
when Language.parser_type == 'korean'):

* Language.kiwi_tokenizer_mode:
    - 'morpheme' : split into individual morphemes (e.g. 예상 + 하 + 었 + 는데).
                   Default and recommended for new learners.
    - 'lemma'    : group morphemes of the same 어절 so that the surface
                   forms of predicate morphemes (V*/XSV) are replaced with
                   their dictionary lemma (e.g. 예상했었는데 → 예상하다).
                   Useful if you prefer to learn the base verb form.
    - 'eojeol'   : entire 어절 (whitespace-delimited block) is kept as a
                   single token (original SpaceDelimited behaviour).

* Language.kiwi_filter_particles (bool):
    When True, grammatical morphemes (particles J*, endings E*) are
    marked as non-word tokens in the output so they still appear in the
    text but are not independently clickable / status-highlighted.

* Language.kiwi_join_compound_nouns (bool):
    When True, consecutive noun morphemes (NNG/NNP/NNB/SN/SL/etc.)
    within the same 어절 are concatenated into a single token.

* Language.kiwi_stemming (bool):
    When True, `get_lemma` uses Kiwi's lemmatization so inflected forms
    (먹었어, 예상했었는데) resolve to their base dictionary form (먹다,
    예상하다) for the popup / parent term lookup.  When False, get_lemma
    returns None and the raw surface form is used directly.
"""

import re
from typing import List, Optional

from lute.parse.base import ParsedToken, AbstractParser
from lute.settings.current import current_settings


class KoreanParser(AbstractParser):
    """
    Korean parser using kiwipiepy for morphological analysis.

    Text is split into tokens controlled by Language.kiwi_tokenizer_mode.
    Spaces between 어절 are preserved as non-word tokens.

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

    # POS tag prefixes for bound morphemes (조사 / 어미).
    _BOUND_POS_PREFIXES = (
        "J",   # 조사 (particles): JKS, JKC, JKG, JKO, JKB, JKV, JKQ, JX, JC
        "E",   # 어미 (endings): EP, EF, EC, ETN, ETM
    )

    # Tags considered 'nouns' for compound-noun merging.
    _NOUN_TAGS = frozenset({
        "NNG", "NNP", "NNB", "NNBC", "NR", "NP",  # pure Korean nouns / counters
        "SN",  # numbers
        "SL",  # foreign words (mostly English)
        "SH",  # Chinese character / hanja
    })

    # Tags that are 'predicate heads' -> when lemma mode is on, we replace
    # the predicate morpheme surface with its lemma form.
    # Includes verbs (VV), adjectives (VA), light verbs (XSV), etc.
    _PREDICATE_TAGS = frozenset({
        "VV", "VA", "VX", "VCP", "VCN",  # verbs / adjectives / copulas
        "XSV", "XSA",                     # verbal / adjectival suffixes
        "XR",                             # roots (used before 하다 compounds)
    })

    # Sentence-ending punctuation tags.
    _SENTENCE_END_TAGS = frozenset({"SF"})

    # ---- settings helpers ----

    @staticmethod
    def _get_mode(language) -> str:
        """Return the user-selected tokenizer mode, defaulting to morpheme."""
        try:
            mode = getattr(language, "kiwi_tokenizer_mode", None) or "morpheme"
            return mode.lower() if mode else "morpheme"
        except Exception:  # pylint: disable=broad-except
            return "morpheme"

    @staticmethod
    def _bool_setting(language, attr: str, default: bool) -> bool:
        """Boolean getter that gracefully degrades to `default`."""
        try:
            v = getattr(language, attr, None)
            if v is None:
                return default
            return bool(v)
        except Exception:  # pylint: disable=broad-except
            return default

    # ---- POS helpers ----

    def _is_bound_morph(self, morph) -> bool:
        """True if morpheme is a particle or ending (J*/E*)."""
        tag = morph.tag or ""
        return any(tag.startswith(p) for p in self._BOUND_POS_PREFIXES)

    def _is_noun_morph(self, morph) -> bool:
        return (morph.tag or "") in self._NOUN_TAGS

    def _is_predicate_morph(self, morph) -> bool:
        return (morph.tag or "") in self._PREDICATE_TAGS

    def _is_word_morph(self, morph, filter_particles: bool) -> bool:
        """
        True if the morpheme should be a clickable word.

        All morphemes are words except punctuation/symbols.  If
        filter_particles is set, bound morphemes (J*/E*) are also treated
        as non-clickable non-word tokens.
        """
        tag = morph.tag or ""
        if tag in self._SYMBOL_TAGS:
            return False
        if filter_particles and self._is_bound_morph(morph):
            return False
        return True

    def _is_content_morph(self, morph) -> bool:
        """
        True if the morpheme is a content morpheme worth keeping for
        lemma extraction (skips particles, endings, symbols).
        """
        tag = morph.tag or ""
        if tag in self._SYMBOL_TAGS:
            return False
        return not self._is_bound_morph(morph)

    # ---- morpheme grouping strategies ----

    def _group_eojeol(self, morphs):
        """
        Given a list of morphemes belonging to the SAME 어절
        (consecutive non-space tokens with no position gap), return
        a list of (surface_form, representative_morph_for_tag,
        representative_morph_for_lemma) tuples.

        The representative morphs are used by higher-level code to decide
        the is_word / is_end_of_sentence flags and, optionally, the
        lemma output.

        Mode 'eojeol'   : single token per 어절.
        Mode 'lemma'    : predicate stems merged into single lemma form,
                          particles/endings kept as separate tokens.
        Mode 'morpheme' : one token per morphological morpheme.
        """
        # Nothing to do for empty lists.
        if not morphs:
            return []

        mode = self._mode  # set on the instance by get_parsed_tokens
        join_compound = self._join_compound

        # --- 'eojeol' mode: return the entire surface as one token. ---
        if mode == "eojeol":
            # Build up the raw surface string in-order.
            surface = "".join(m.form for m in morphs)
            # The 'representative' morph is whatever the first non-symbol
            # morph is (so that sentence-end detection still fires via
            # the representative if the user uses SF as part of the
            # 어절, which shouldn't happen but we guard anyway).
            rep = next((m for m in morphs if (m.tag or "") not in self._SYMBOL_TAGS), morphs[0])
            return [(surface, rep, rep)]

        # --- 'lemma' mode --------------------------------------------------
        # Predicate morphemes within one 어절 are merged with their
        # preceding noun/XR head when appropriate, producing a single
        # lemma token.  Compound noun morphemes are also merged if the
        # option is set.
        if mode == "lemma":
            out = []
            i = 0
            n = len(morphs)
            while i < n:
                m = morphs[i]

                # 1) If compound-noun merging is on, gobble all consecutive
                #    noun morphemes starting at i into one surface token.
                if join_compound and self._is_noun_morph(m):
                    start = i
                    while i + 1 < n and self._is_noun_morph(morphs[i + 1]):
                        i += 1
                    surface = "".join(x.form for x in morphs[start:i + 1])
                    out.append((surface, morphs[start], morphs[start]))
                    i += 1
                    continue

                # 2) If this morph is a predicate head, gather the whole
                #    predicate phrase (head + following bound E* morphemes)
                #    and emit the lemma form (e.g. 예상했다 → 예상하다).
                if self._is_predicate_morph(m):
                    # Find all morphemes in the same 어절 that belong to
                    # the same predicate chain: up to (but not including)
                    # the next non-predicate / non-E* morpheme.
                    start = i
                    # predicate chain = head + E morphemes
                    j = i  # last index that is part of the predicate
                    # Already include the head, walk forward while it's
                    # either another predicate suffix or an ending.
                    while j + 1 < n:
                        nxt = morphs[j + 1]
                        if (self._is_predicate_morph(nxt)
                                or (nxt.tag or "").startswith("E")):
                            j += 1
                        else:
                            break
                    # Concatenate all surface forms to get the full
                    # predicative expression surface (e.g. 했었는데).
                    # The lemma comes from the first predicate head.
                    head_lemma = getattr(m, "lemma", None) or m.form
                    head_lemma = head_lemma if head_lemma != "*" else m.form

                    surface_chunks = []
                    # For the very first head, use the lemma form of the
                    # head instead of its surface if the head was inflected
                    # in a weird way.  For predicate/ending morphemes
                    # AFTER the head, we only attach them if they are NOT
                    # endings (E*), because endings are inflection and the
                    # dictionary form already contains the -다 suffix.
                    #
                    # Simpler rule that matches most Lute use-cases:
                    #   predicate token surface = lemma of head verb.
                    # This is what the user asked for: 예상했었는데 → 예상하다.
                    surface = head_lemma
                    if surface == m.form and j == start:
                        # no inflection detected and single-head predicate,
                        # still render as lemma form to keep the user's
                        # mental model consistent ("dictionary form").
                        surface = head_lemma

                    # The representative morph for EOS/symbol purposes is
                    # the last morpheme in the chain so that sentence-end
                    # endings (EF/SF) correctly trigger EOS.
                    rep_tag = morphs[j]
                    rep_lemma = m  # lemma representative is always the head
                    out.append((surface, rep_tag, rep_lemma))
                    i = j + 1
                    continue

                # 3) Default: single morpheme per token.
                out.append((m.form, m, m))
                i += 1
            return out

        # --- 'morpheme' mode (default) --------------------------------------
        # One token per morpheme, but optionally merge consecutive pure
        # noun morphemes into a single compound-noun token.
        out = []
        i = 0
        n = len(morphs)
        while i < n:
            m = morphs[i]
            if join_compound and self._is_noun_morph(m):
                start = i
                while i + 1 < n and self._is_noun_morph(morphs[i + 1]):
                    i += 1
                surface = "".join(x.form for x in morphs[start:i + 1])
                out.append((surface, morphs[start], morphs[start]))
                i += 1
                continue
            out.append((m.form, m, m))
            i += 1
        return out

    # ---- tokenization ----

    def get_parsed_tokens(self, text: str, language) -> List[ParsedToken]:
        """
        Parse text into tokens using Kiwi, respecting the language
        settings for granularity / particle filtering / compound merging.
        """
        text = re.sub(r"[ \t]+", " ", text).strip()

        # Read the language settings once.
        self._mode = self._get_mode(language)
        self._join_compound = self._bool_setting(language, "kiwi_join_compound_nouns", False)
        self._filter_particles = self._bool_setting(language, "kiwi_filter_particles", False)

        # Build the set of sentence-ending characters from language settings.
        splitchar = ""
        if language and getattr(language, "regexp_split_sentences", None):
            splitchar = language.regexp_split_sentences.strip()
        if not splitchar:
            splitchar = ".!?。？！"

        kiwi = self._get_kiwi()
        tokens: List[ParsedToken] = []

        for para in text.split("\n"):
            para = para.strip()
            if para:
                all_morphs = kiwi.tokenize(para)

                # 1) Walk morphemes by 어절.  Two consecutive morphemes
                #    are in the same 어절 if there is no positional gap
                #    between them (i.e. next.start <= prev.start + prev.len).
                eojeols = []  # list[list[Morph]]
                current = []
                prev_end = 0
                for m in all_morphs:
                    if current and m.start > prev_end:
                        eojeols.append(current)
                        current = []
                    current.append(m)
                    new_end = m.start + m.len
                    if new_end > prev_end:
                        prev_end = new_end
                if current:
                    eojeols.append(current)

                # 2) For each 어절, convert into grouped tokens, and
                #    insert a space token between consecutive 어절.
                para_prev_end = 0
                for idx, morphs in enumerate(eojeols):
                    if idx > 0:
                        tokens.append(ParsedToken(" ", False, False))
                    grouped = self._group_eojeol(morphs)
                    for surface, rep_tag_morph, _rep_lemma_morph in grouped:
                        is_word = self._is_word_morph(rep_tag_morph, self._filter_particles)
                        is_eos = (rep_tag_morph.tag in self._SENTENCE_END_TAGS) or any(
                            c in splitchar for c in surface
                        )
                        tokens.append(ParsedToken(surface, is_word, is_eos))

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

    def get_lemma(self, text: str, language: Optional[object] = None):
        """
        Get the dictionary/lemma form of the given text.

        If Language.kiwi_stemming is explicitly False, returns None.
        Otherwise uses Kiwi's lemmatization so that:
            먹었어 → 먹다
            예상했었는데 → 예상하다

        Returns None if the text is already in its base form or the
        lemmatized string is identical to the input.
        """
        # Respect the stemming toggle (if a language is passed in).
        if language is not None:
            if not self._bool_setting(language, "kiwi_stemming", True):
                return None

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
