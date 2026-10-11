"""
Flask-wtf forms.
"""

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    IntegerField,
    BooleanField,
    SelectField,
    SelectMultipleField,
    FormField,
    FieldList,
    Form,
    ValidationError,
)
from wtforms.validators import DataRequired
from wtforms.widgets import ListWidget, CheckboxInput
from lute.language.langtags import tag_choices
from lute.models.language import LanguageDictionary


class MultiCheckboxField(SelectMultipleField):
    """
    A SelectMultipleField rendered as a list of checkboxes.

    The value is a list of the checked choice values, which is what
    Language.hidden_grammar_levels expects.
    """

    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class LanguageDictionaryForm(Form):
    """
    Language dictionary form, nested in Language form.
    """

    usefor = SelectField(
        choices=[("terms", "Terms"), ("sentences", "Sentences")],
        render_kw={"title": "Use dictionary for"},
    )
    dicttype = SelectField(
        choices=[
            ("embeddedhtml", "Embedded"),
            ("popuphtml", "Pop-up window"),
            ("localdict", "Local"),
        ],
        render_kw={"title": "Show as"},
    )
    dicturi = StringField(
        "URL",
        validators=[DataRequired()],
        render_kw={
            "list": "dict-pool-files",
            "title": (
                "Online dictionaries: a lookup URL with [LUTE] for the term.  "
                "Local dictionaries: a pool file name (see Settings > Local "
                "dictionaries)"
            ),
        },
    )
    is_active = BooleanField("Is active", render_kw={"title": "Is active?"})
    # Kept configured but no tab in the reading page's dictionary strip.
    is_hidden = BooleanField(
        "Hide",
        render_kw={"title": "Keep this dictionary but hide its tab"},
    )
    sort_order = IntegerField("Sort", render_kw={"style": "display: none"})


class LanguageForm(FlaskForm):
    """
    Language.
    """

    name = StringField("Name", validators=[DataRequired()])
    dictionaries = FieldList(
        FormField(LanguageDictionaryForm, default=LanguageDictionary)
    )
    show_romanization = BooleanField("Show Pronunciation field")
    right_to_left = BooleanField("Right-to-left")

    # Note!  The choices have to be set in the routes!
    # I originally had "choices=lute.parse.registry.supported_parsers()",
    # but it never worked: the Japanese mecab parser was excluded.
    # Possible coder error, not sure, but setting the choices at
    # form creation time works.
    parser_type = SelectField("Parse as", choices=[("tbd", "tbd")])

    character_substitutions = StringField("Character substitutions")

    regexp_split_sentences = StringField(
        "Split sentences at (default: all Unicode sentence terminators)"
    )
    exceptions_split_sentences = StringField("Split sentence exceptions")
    word_characters = StringField(
        "Word characters (default: all Unicode letters and marks)"
    )

    # --- Optional per-language TTS / translation overrides.
    # Rendered by the generic field loop in _form.html.
    tts_lang = SelectField(
        "TTS Language",
        choices=tag_choices("auto-detect from language name"),
        default="",
    )
    translate_target_lang = SelectField(
        "Google Translate Target",
        choices=tag_choices("browser language"),
        default="",
    )
    grammar_translate_lang = SelectField(
        "Grammar Analysis Language",
        choices=[("en", "English"), ("zh", "中文"), ("ko", "한국어")],
        default="en",
    )
    # --- Local (MDX) dictionaries.
    # Local dictionaries are ordinary rows in the dictionary list above
    # (type "Local"; the URL is a pool file name -- the pool is a folder
    # on the server, managed on the settings page, see lute/dict), so
    # there is nothing to choose here: several are allowed, and they
    # order and hide like every other tab.

    term_popup_show_dict_gloss = BooleanField(
        "Show dictionary definitions in the term popup",
        render_kw={
            "title": (
                "Add this language's dictionary definition to the popup, for "
                "terms with no translation yet.  The reading page's dictionary "
                "tab is unaffected."
            )
        },
    )
    # What fills a new term's translation.  'local_dict' tries the local
    # dictionaries in tab order and falls back to online translation
    # when none has an entry -- only meaningful when the language has
    # dictionaries of type Local above.
    term_translation_source = SelectField(
        "Term translation source",
        choices=[("online", "Online translation"), ("local_dict", "Local dictionary")],
        default="online",
        render_kw={
            "title": (
                "What auto-fills a new term's translation: online machine "
                "translation, or this language's local dictionary first "
                "(online as fallback)"
            )
        },
    )

    # Grammar levels / groups to hide in the reading-page panel.  The
    # choices depend on the language's grammar taxonomy (JLPT, TOPIK,
    # CEFR) plus the Japanese aggregate rows, so they are set in the
    # routes -- same reason as parser_type above.
    hidden_grammar_levels = MultiCheckboxField(
        "Hide in grammar panel",
        choices=[],
        default=[],
        render_kw={"style": "list-style:none; padding-left:0; margin:0;"},
    )

    # --- Korean / Kiwi-specific settings.
    # These fields are only rendered when the parser_type is 'lute_korean'.

    kiwi_tokenizer_mode_choices = [
        ("morpheme", "Fine-grained / Morpheme (예상 + 하 + 었 + 는데"),
        ("lemma", "Medium / Dictionary form (예상하다)"),
        ("eojeol", "Coarse / Whole 어절 (예상했었는데)"),
    ]
    kiwi_tokenizer_mode = SelectField(
        "切词模式 (Tokenizer Mode)",
        choices=kiwi_tokenizer_mode_choices,
        default="morpheme",
    )
    kiwi_stemming = BooleanField("自动提取词典原形 (Stemming / Lemmatization)")
    kiwi_filter_particles = BooleanField("过滤语法助词 (Filter Particles: 은/는, 이/가…)")
    kiwi_join_compound_nouns = BooleanField("合并复合名词 (Join Compound Nouns)")

    def validate_dictionaries(self, field):  # pylint: disable=unused-argument
        "Dictionaries must be valid."

        # raise ValueError(self.dictionaries.data) # debugging
        def _get_actives(usefor):
            "Return dictionaries."
            return [
                d
                for d in self.dictionaries.data
                if d.get("usefor", "") == usefor and d.get("is_active")
            ]

        # A Local row's URL is a file name in the server's dictionary
        # pool, not a web URL: it round-trips into filesystem paths, so
        # it is held to the pool's own name rules.
        from lute.dict.service import is_pool_name  # pylint: disable=import-outside-toplevel

        for d in self.dictionaries.data:
            if d.get("dicttype", "") == "localdict" and not is_pool_name(
                (d.get("dicturi") or "").strip()
            ):
                raise ValidationError(
                    f'"{d.get("dicturi")}" is not a valid Local dictionary '
                    "name -- use a file name from Settings > Local "
                    "dictionaries, ending in .db."
                )

        term_dicts = _get_actives("terms")
        sentence_dicts = _get_actives("sentences")
        if len(term_dicts) == 0:
            raise ValidationError("Please add an active Terms dictionary")
        if len(sentence_dicts) == 0:
            raise ValidationError("Please add an active Sentences dictionary")
