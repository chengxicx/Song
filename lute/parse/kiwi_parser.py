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
    # Includes verbs (VV), adjectives (VA), light verbs (XSV/XR하 compounds),
    # etc.  XR alone (e.g. 독립 in 독립하다) is NOT a predicate head, but if
    # followed immediately by XSV (하) or XSA (되/하) it forms a predicate
    # compound together, so we treat the (XR/NNG + XSV/XSA) pair as a single
    # logical predicate head.
    _PREDICATE_TAGS = frozenset({
        "VV", "VA", "VX", "VCP", "VCN",  # verbs / adjectives / copulas
        "XSV", "XSA",                     # verbal / adjectival suffixes (하되…)
    })

    # Morphs that, when they appear immediately BEFORE an XSV/XSA, merge with
    # it to form a dictionary-form compound verb (e.g. 예상/NNG + 하/XSV + E*
    # → 예상하다).
    _LIGHT_VERB_NOUN_PRE_TAGS = frozenset({
        "XR",   # 依存名词 / Chinese character roots (e.g. 예상 -> 豫想)
        "NNG",  # general nouns used before 하다 (독립 + 하다 = 독립하다)
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

    @staticmethod
    def _normalise_predicate_lemma(lemma: str) -> str:
        """
        Normalise a predicate lemma so that it ends with -다/-하다/-되다
        consistently.  Kiwi sometimes returns the stem without the final
        '-다' for light-verb suffixes: e.g. 하/XSV has lemma='하' not '하다'.
        We append '다' so that:
            하   → 하다
            되   → 되다
            하   → 하다  (after 하/XSA from '행복하다')
            가다 → 가다  (already a proper verb, untouched)
        """
        if not lemma:
            return lemma
        # Short predicate stems (single syllable) that haven't got -다 yet.
        # Common cases: 하(다), 되(다), 이(다: copula), 아니(다).
        if len(lemma) <= 2 and not lemma.endswith("다"):
            return lemma + "다"
        return lemma

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

                # 2) Predicate head detection:
                #    - Case A: VV/VA/VX/… → the morph itself is a predicate.
                #      Walk forward through predicate suffixes + endings
                #      to build the inflected part; emit lemma of head.
                #    - Case B: XR/NNG immediately followed by XSV/XSA
                #      (light-verb compounds: 독립+하다 / 행복+하다 / 예상+하다).
                #      In this case we merge the preceding noun + light-verb
                #      suffix into a dictionary-form surface and walk all
                #      following endings into the same logical token.

                # ---- Case B (light-verb compound) first ----------------
                if ((m.tag or "") in self._LIGHT_VERB_NOUN_PRE_TAGS
                        and i + 1 < n
                        and (morphs[i + 1].tag or "") in ("XSV", "XSA")):
                    noun_morph = m
                    lv_suffix = morphs[i + 1]
                    # Predicate chain starts at the noun index i, but the
                    # logical head for walking endings is the light-verb
                    # suffix index i+1.
                    chain_start = i
                    j = i + 1  # current last-in-chain index
                    # Walk forward: extra predicate suffixes or endings.
                    while j + 1 < n:
                        nxt = morphs[j + 1]
                        if (self._is_predicate_morph(nxt)
                                or (nxt.tag or "").startswith("E")):
                            j += 1
                        else:
                            break
                    # Build the combined dictionary-form surface:
                    #   noun_lemma + light_verb_suffix_normalised
                    noun_lemma = getattr(noun_morph, "lemma", None) or noun_morph.form
                    noun_lemma = noun_lemma if noun_lemma != "*" else noun_morph.form
                    lv_lemma = getattr(lv_suffix, "lemma", None) or lv_suffix.form
                    lv_lemma = lv_lemma if lv_lemma != "*" else lv_suffix.form
                    lv_lemma = self._normalise_predicate_lemma(lv_lemma)
                    surface = noun_lemma + lv_lemma

                    rep_tag = morphs[j]
                    # The lemma-representative for light-verb compounds is
                    # the light-verb suffix so we always normalise to 하다.
                    out.append((surface, rep_tag, lv_suffix))
                    i = j + 1
                    continue

                # ---- Case A (standalone predicate) --------------------
                if self._is_predicate_morph(m):
                    start = i
                    j = i
                    while j + 1 < n:
                        nxt = morphs[j + 1]
                        if (self._is_predicate_morph(nxt)
                                or (nxt.tag or "").startswith("E")):
                            j += 1
                        else:
                            break
                    head_lemma = getattr(m, "lemma", None) or m.form
                    head_lemma = head_lemma if head_lemma != "*" else m.form
                    head_lemma = self._normalise_predicate_lemma(head_lemma)
                    surface = head_lemma
                    rep_tag = morphs[j]
                    out.append((surface, rep_tag, m))
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
        i = 0
        n = len(result)
        while i < n:
            m = result[i]

            # Light-verb compound: XR/NNG + XSV/XSA → merge + normalise 하→하다 etc.
            if ((m.tag or "") in self._LIGHT_VERB_NOUN_PRE_TAGS
                    and i + 1 < n
                    and (result[i + 1].tag or "") in ("XSV", "XSA")):
                noun_lemma = getattr(m, "lemma", None) or m.form
                noun_lemma = noun_lemma if noun_lemma != "*" else m.form
                lv_suffix = result[i + 1]
                lv_lemma = getattr(lv_suffix, "lemma", None) or lv_suffix.form
                lv_lemma = lv_lemma if lv_lemma != "*" else lv_suffix.form
                lv_lemma = self._normalise_predicate_lemma(lv_lemma)
                lemmas.append(noun_lemma + lv_lemma)
                i += 2
                continue

            if not self._is_content_morph(m):
                i += 1
                continue
            surface = m.form
            lemma = getattr(m, "lemma", None) or surface
            if lemma and lemma != "*":
                # If it's a standalone light-verb suffix, normalise it.
                if (m.tag or "") in ("XSV", "XSA"):
                    lemma = self._normalise_predicate_lemma(lemma)
                # Standalone predicate tags that sometimes miss -다: VV/VA/etc.
                elif self._is_predicate_morph(m):
                    lemma = self._normalise_predicate_lemma(lemma)
                lemmas.append(lemma)
            else:
                lemmas.append(surface)
            i += 1

        if not lemmas:
            return None

        ret = "".join(lemmas).strip()
        if ret in ("", text):
            return None
        return ret
