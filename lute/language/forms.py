"""
Flask-wtf forms.
"""

from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    IntegerField,
    BooleanField,
    SelectField,
    FormField,
    FieldList,
    Form,
    ValidationError,
)
from wtforms.validators import DataRequired, Optional, Regexp
from lute.models.language import LanguageDictionary


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
        ],
        render_kw={"title": "Show as"},
    )
    dicturi = StringField("URL", validators=[DataRequired()])
    is_active = BooleanField("Is active", render_kw={"title": "Is active?"})
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
    _lang_tag_regex = r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$"
    tts_lang = StringField(
        "TTS 语言代码 (TTS Language Tag)",
        validators=[
            Optional(),
            Regexp(
                _lang_tag_regex,
                message='格式应为 BCP-47 标签，如 "zh-HK"、"yue"、"ja-JP"',
            ),
        ],
        render_kw={"placeholder": "例如 zh-HK / yue / ja-JP，留空则按语言名自动推断"},
    )
    translate_target_lang = StringField(
        "Google 翻译目标语言 (Translate Target)",
        validators=[
            Optional(),
            Regexp(
                _lang_tag_regex,
                message='格式应为语言代码，如 "zh-CN"、"en"',
            ),
        ],
        render_kw={"placeholder": "例如 zh-CN，留空则使用浏览器语言"},
    )

    # --- Korean / Kiwi-specific settings.
    # These fields are only rendered when the parser_type is 'korean'.

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
    kiwi_stemming = BooleanField(
        "自动提取词典原形 (Stemming / Lemmatization)"
    )
    kiwi_filter_particles = BooleanField(
        "过滤语法助词 (Filter Particles: 은/는, 이/가…)"
    )
    kiwi_join_compound_nouns = BooleanField(
        "合并复合名词 (Join Compound Nouns)"
    )

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

        term_dicts = _get_actives("terms")
        sentence_dicts = _get_actives("sentences")
        if len(term_dicts) == 0:
            raise ValidationError("Please add an active Terms dictionary")
        if len(sentence_dicts) == 0:
            raise ValidationError("Please add an active Sentences dictionary")
