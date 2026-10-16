"""
Language entity.
"""

import re
from lute.db import db
from lute.parse.registry import get_parser, is_supported


def _local_dict_tab_entry(lang, d):
    """
    The tab entry for one 'localdict' dictionary row, or None when the
    dictionary's file is no longer installed in the server's pool.

    Lives at module level with a lazy import: the dict service needs the
    Flask app (static folder), and importing it at module load time
    would make lute.models depend on lute.dict at import time.
    """
    from lute.dict import (
        service as dict_service,
    )  # pylint: disable=import-outside-toplevel

    filename = d.dicturi
    if not dict_service.is_pool_name(filename) or not dict_service.is_installed(
        filename
    ):
        return None
    info = dict_service.status(filename)
    title = info.get("title") or None
    label = title or filename[: -len(".db")]
    tooltip = f"{title} ({filename})" if title else f"Local dictionary: {filename}"
    return {
        "url": f"/read/localdict/{lang.id}/{filename}/[LUTE]",
        "dicttype": "localdict",
        "label": label,
        "title": tooltip,
    }


class LanguageDictionary(db.Model):
    """
    Language dictionary.
    """

    __tablename__ = "languagedicts"

    id = db.Column("LdID", db.SmallInteger, primary_key=True)
    language_id = db.Column(
        "LdLgID", db.Integer, db.ForeignKey("languages.LgID"), nullable=False
    )
    language = db.relationship("Language", back_populates="dictionaries")
    usefor = db.Column("LdUseFor", db.String(20), nullable=False)
    dicttype = db.Column("LdType", db.String(20), nullable=False)
    dicturi = db.Column("LdDictURI", db.String(200), nullable=False)
    is_active = db.Column("LdIsActive", db.Boolean, default=True)
    # Kept configured but no tab in the reading page's dictionary strip:
    # for dictionaries that are rarely used but not worth re-entering.
    is_hidden = db.Column("LdIsHidden", db.Boolean, default=False)
    sort_order = db.Column("LdSortOrder", db.SmallInteger, nullable=False)


class Language(
    db.Model
):  # pylint: disable=too-few-public-methods, too-many-instance-attributes
    """
    Language entity.
    """

    __tablename__ = "languages"

    id = db.Column("LgID", db.SmallInteger, primary_key=True)
    name = db.Column("LgName", db.String(40))

    dictionaries = db.relationship(
        "LanguageDictionary",
        back_populates="language",
        order_by="LanguageDictionary.sort_order",
        lazy="subquery",
        cascade="all, delete-orphan",
    )

    character_substitutions = db.Column("LgCharacterSubstitutions", db.String(500))
    regexp_split_sentences = db.Column("LgRegexpSplitSentences", db.String(500))
    exceptions_split_sentences = db.Column("LgExceptionsSplitSentences", db.String(500))
    _word_characters = db.Column("LgRegexpWordCharacters", db.String(500))
    right_to_left = db.Column("LgRightToLeft", db.Boolean)
    show_romanization = db.Column("LgShowRomanization", db.Boolean)
    # Furigana display on the reading page (Japanese readings above
    # kanji words).  Only acted on for Japanese-capable parsers.
    show_furigana = db.Column("LgShowFurigana", db.Boolean)
    parser_type = db.Column("LgParserType", db.String(20))
    is_active = db.Column("LgIsActive", db.Boolean, default=True)

    # Kiwi (Korean) parser settings.
    # These columns only have meaning when LgParserType = 'lute_korean'.
    kiwi_tokenizer_mode = db.Column(
        "LgKiwiTokenizerMode", db.String(20), default="morpheme"
    )
    kiwi_stemming = db.Column("LgKiwiStemming", db.Boolean, default=True)
    kiwi_filter_particles = db.Column(
        "LgKiwiFilterParticles", db.Boolean, default=False
    )
    kiwi_join_compound_nouns = db.Column(
        "LgKiwiJoinCompoundNouns", db.Boolean, default=False
    )

    # Optional per-language overrides for TTS and term translation.
    # When empty, the TTS tag is derived from the language name, and
    # the translation target falls back to the browser UI language.
    tts_lang = db.Column("LgTTSLang", db.String(20))
    translate_target_lang = db.Column("LgTranslateTargetLang", db.String(20))
    # Display language for grammar analysis results ("en" default, "zh" Chinese).
    grammar_translate_lang = db.Column("LgGrammarTranslateLang", db.String(20))

    # Grammar-panel groups the reader hides for this language: a
    # pipe-delimited list of level tokens (JLPT N5..N1, TOPIK bands,
    # CEFR A1..C2) and the Japanese aggregate keys (basic_forms,
    # basic_particles).  One string column backs the
    # `hidden_grammar_levels` list property below.
    _hidden_grammar_levels = db.Column("LgHiddenGrammarLevels", db.String(500))

    # The local (MDX) dictionary this language reads used to be a
    # single file name here (LgLocalDict); it is now one or more
    # 'localdict' rows in self.dictionaries -- see active_dict_uris.
    # The column survives in the schema (back-filled by migration
    # 20261012) but nothing reads or writes it any more.

    # Whether the term popup adds this language's local dictionary
    # definition, for terms with no translation yet.  Per language
    # rather than global: a reader of two languages rarely wants the
    # same answer for both.
    # Whether the term popup adds this language's local dictionary
    # definition, for terms with no translation yet.  Per language
    # rather than global: a reader of two languages rarely wants the
    # same answer for both.
    term_popup_show_dict_gloss = db.Column(
        "LgTermPopupShowDictGloss", db.Boolean, default=True
    )

    # What fills a new term's translation: 'online' (machine
    # translation, the old behaviour) or 'local_dict' -- the language's
    # local dictionaries (see the 'localdict' rows in self.dictionaries)
    # in tab order, online as fallback when none of them has an entry.
    term_translation_source = db.Column(
        "LgTermTranslationSource", db.String(20), default="online"
    )

    def __init__(self):
        self.character_substitutions = "´='|`='|’='|‘='|...=…|..=‥"
        self.regexp_split_sentences = ".!?"
        self.exceptions_split_sentences = "Mr.|Mrs.|Dr.|[A-Z].|Vd.|Vds."
        self.word_characters = "a-zA-ZÀ-ÖØ-öø-ȳáéíóúÁÉÍÓÚñÑ"
        self.right_to_left = False
        self.show_romanization = False
        self.show_furigana = False
        self.parser_type = "spacedel"
        self.dictionaries = []
        self.is_active = True
        self.kiwi_tokenizer_mode = "morpheme"
        self.kiwi_stemming = True
        self.kiwi_filter_particles = False
        self.kiwi_join_compound_nouns = False
        self.term_popup_show_dict_gloss = True
        self.term_translation_source = "online"

    def __repr__(self):
        return f"<Language {self.id} '{self.name}'>"

    def _get_python_regex_pattern(self, s):
        """
        Old Lute v2 ran in php, so the language word chars regex
        could look like this:

        x{0600}-x{06FF}x{FE70}-x{FEFC}  (where x = backslash-x)

        This needs to be converted to the python equivalent, e.g.

        u0600-u06FFuFE70-uFEFC  (where u = backslash-u)
        """

        def convert_match(match):
            # Convert backslash-x{XXXX} to backslash-uXXXX
            hex_value = match.group(1)
            return f"\\u{hex_value}"

        ret = re.sub(r"\\x{([0-9A-Fa-f]+)}", convert_match, s)
        return ret

    @property
    def word_characters(self):
        return self._get_python_regex_pattern(self._word_characters)

    @word_characters.setter
    def word_characters(self, s):
        self._word_characters = self._get_python_regex_pattern(s)

    @property
    def hidden_grammar_levels(self):
        """
        Grammar levels/groups hidden in the reading-page grammar panel.

        Returned as a list of tokens; each is either a level name
        ("N5", "TOPIK 1-2", "A1") or one of the Japanese aggregate keys
        ("basic_forms", "basic_particles").  Empty means nothing is
        hidden.
        """
        raw = self._hidden_grammar_levels or ""
        return [t.strip() for t in raw.split("|") if t.strip()]

    @hidden_grammar_levels.setter
    def hidden_grammar_levels(self, tokens):
        if not tokens:
            self._hidden_grammar_levels = ""
        elif isinstance(tokens, str):
            self._hidden_grammar_levels = tokens
        else:
            self._hidden_grammar_levels = "|".join(
                t.strip() for t in tokens if t and t.strip()
            )

    def active_dict_uris(self, use_for):
        """
        Get sorted uris for active dicts of correct type.

        Hidden dicts stay configured (and pass the "must have an active
        dictionary" validation) but offer no tab anywhere.

        'localdict' rows are converted on the way out: their dicturi is
        a file name in the server's dictionary pool (see lute/dict), and
        the tab needs a URL on this server plus its display name.  A
        local dictionary whose file is no longer installed offers no
        tab -- same as the old single-dictionary behaviour.
        """
        # Hidden dicts stay configured (and pass the "must have an active
        # dictionary" validation) but offer no tab anywhere.
        actives = [
            d
            for d in self.dictionaries
            if d.is_active and not d.is_hidden and d.usefor == use_for
        ]
        sorted_actives = sorted(actives, key=lambda x: x.sort_order)
        ret = []
        for d in sorted_actives:
            if d.dicttype == "localdict":
                entry = _local_dict_tab_entry(self, d)
                if entry is not None:
                    ret.append(entry)
            else:
                ret.append({"url": d.dicturi, "dicttype": d.dicttype})
        return ret

    def local_dict_files(self):
        """
        The pool file names of this language's active, visible local
        dictionaries, in tab order -- the order the term popup's gloss
        and the term form's local-dict fill try them in.
        """
        return [
            d.dicturi
            for d in sorted(self.dictionaries, key=lambda x: x.sort_order)
            if d.dicttype == "localdict"
            and d.is_active
            and not d.is_hidden
            and d.usefor == "terms"
        ]

    @property
    def sentence_dict_uris(self):
        return self.active_dict_uris("sentences")

    @property
    def parser(self):
        "Note: this throws if the parser is not supported!!!"
        return get_parser(self.parser_type)

    @property
    def is_supported(self):
        "True if the language's parser is supported."
        return is_supported(self.parser_type)

    def get_parsed_tokens(self, s):
        return self.parser.get_parsed_tokens(s, self)

    def get_lowercase(self, s) -> str:
        return self.parser.get_lowercase(s)

    def to_dict(self):
        "Return dictionary of data, for serialization."
        ret = {}
        ret["name"] = self.name
        ret["dictionaries"] = []
        for d in self.dictionaries:
            dd = {}
            dd["for"] = d.usefor
            dd["type"] = d.dicttype.replace("html", "")
            dd["url"] = d.dicturi
            dd["active"] = d.is_active
            ret["dictionaries"].append(dd)
        ret["show_romanization"] = self.show_romanization
        ret["show_furigana"] = self.show_furigana
        ret["right_to_left"] = self.right_to_left
        ret["parser_type"] = self.parser_type
        ret["character_substitutions"] = self.character_substitutions
        ret["split_sentences"] = self.regexp_split_sentences
        ret["split_sentence_exceptions"] = self.exceptions_split_sentences
        ret["word_chars"] = self.word_characters
        ret["tts_lang"] = self.tts_lang
        ret["translate_target_lang"] = self.translate_target_lang
        return ret

    @staticmethod
    def from_dict(d):
        "Create new Language from dictionary d."

        lang = Language()

        def load(key, method):
            if key in d:
                val = d[key]
                # Handle boolean values
                if isinstance(val, str):
                    temp = val.lower()
                    if temp == "true":
                        val = True
                    elif temp == "false":
                        val = False
                setattr(lang, method, val)

        # Define mappings for fields
        mappings = {
            "name": "name",
            "show_romanization": "show_romanization",
            "show_furigana": "show_furigana",
            "right_to_left": "right_to_left",
            "parser_type": "parser_type",
            "character_substitutions": "character_substitutions",
            "split_sentences": "regexp_split_sentences",
            "split_sentence_exceptions": "exceptions_split_sentences",
            "word_chars": "word_characters",
            "tts_lang": "tts_lang",
            "translate_target_lang": "translate_target_lang",
            "grammar_translate_lang": "grammar_translate_lang",
        }

        for key in d.keys():
            funcname = mappings.get(key, "")
            if funcname:
                load(key, funcname)

        # A definition may name a preferred parser and a fallback for
        # machines where the preferred one isn't installed.  This
        # matters when the preferred parser comes from an optional
        # dependency (e.g. sudachipy) while the fallback is a core one,
        # so the predefined language stays loadable instead of
        # vanishing from the language list.
        fallback = d.get("parser_type_fallback")
        if fallback and not lang.is_supported and is_supported(fallback):
            lang.parser_type = fallback

        ld_sort = 1
        for ld_data in d["dictionaries"]:
            dtype = ld_data["type"]
            if dtype == "embedded":
                dtype = "embeddedhtml"
            elif dtype == "popup":
                dtype = "popuphtml"
            elif dtype in ("localdict", "local"):
                dtype = "localdict"
            else:
                raise ValueError(f"Invalid dictionary type {dtype}")

            ld = LanguageDictionary()
            # ld.language = lang -- if you do this, the dict is added twice.
            ld.usefor = ld_data["for"]
            ld.dicttype = dtype
            ld.dicturi = ld_data["url"]
            ld.is_active = ld_data.get("active", True)

            ld.sort_order = ld_sort
            ld_sort += 1
            lang.dictionaries.append(ld)

        return lang
