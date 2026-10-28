"""
Settings form.
"""

import os
from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, IntegerField, TextAreaField, SelectField
from wtforms.validators import InputRequired, NumberRange
from wtforms import ValidationError

from lute.i18n import lazy_t


class UserSettingsForm(FlaskForm):
    """
    Settings.

    Note the field names here must match the keys in the settings table.
    """

    backup_enabled = BooleanField(lazy_t("Backup Enabled"))
    backup_dir = StringField(lazy_t("Backup directory"))
    backup_auto = BooleanField(lazy_t("Run backups automatically (daily)"))
    backup_warn = BooleanField(lazy_t("Warn if backup hasn't run in a week"))
    backup_count = IntegerField(
        lazy_t("Retain backup count"),
        validators=[InputRequired(), NumberRange(min=1)],
        render_kw={
            "title": lazy_t(
                "Count of zipfiles to retain, oldest files are deleted first"
            )
        },
    )

    current_theme = SelectField(lazy_t("Theme"))
    # UI display language (lute.i18n).  Choices are set in the route.
    ui_language = SelectField(lazy_t("UI language"))
    custom_styles = TextAreaField(lazy_t("Custom styles"))
    show_highlights = BooleanField(lazy_t("Highlight terms by status"))

    open_popup_in_new_tab = BooleanField(lazy_t("Open popup in new tab"))
    stop_audio_on_term_form_open = BooleanField(lazy_t("Stop audio on term form open"))
    show_streak_on_home = BooleanField(lazy_t("Show reading streak on home page"))
    stats_calc_sample_size = IntegerField(
        lazy_t("Book stats page sample size"),
        validators=[InputRequired(), NumberRange(min=1, max=500)],
        render_kw={
            "title": lazy_t("Number of pages to use for book stats calculation.")
        },
    )

    term_popup_promote_parent_translation = BooleanField(
        lazy_t("Promote parent translation to term translation if possible")
    )
    term_popup_show_components = BooleanField(lazy_t("Show component terms"))
    # The local dictionary gloss used to be a global setting here.  It is
    # per language now (Language.term_popup_show_dict_gloss): one user
    # reads several languages, and a single switch could only ever be
    # right for one of them.

    mecab_path = StringField(lazy_t("MECAB_PATH environment variable"))
    dict_choices = [
        ("auto", lazy_t("Auto-detect")),
        ("ipadic", lazy_t("IPADIC")),
        ("unidic", lazy_t("Unidic")),
    ]
    japanese_dict = SelectField(lazy_t("MeCab dictionary"), choices=dict_choices)
    reading_choices = [
        ("katakana", lazy_t("Katakana")),
        ("hiragana", lazy_t("Hiragana")),
        ("alphabet", lazy_t("Romaji")),
    ]
    japanese_reading = SelectField(
        lazy_t("Pronunciation characters"), choices=reading_choices
    )

    sudachi_dict_choices = [
        ("small", lazy_t("Small")),
        ("core", lazy_t("Core")),
        ("full", lazy_t("Full")),
    ]
    japanese_sudachi_dict = SelectField(
        lazy_t("Sudachi dictionary"), choices=sudachi_dict_choices, default="core"
    )
    sudachi_mode_choices = [
        ("A", lazy_t("A (shortest)")),
        ("B", lazy_t("B (middle)")),
        ("C", lazy_t("C (longest)")),
    ]
    japanese_sudachi_mode = SelectField(
        lazy_t("Sudachi split mode"), choices=sudachi_mode_choices, default="C"
    )

    use_ankiconnect = BooleanField(lazy_t("Enable export using AnkiConnect"))
    ankiconnect_url = StringField(
        lazy_t("AnkiConnect URL"), validators=[InputRequired()]
    )

    tts_hover_pronunciation = BooleanField(lazy_t("Pronounce word on hover"))
    tts_hover_delay_choices = [
        ("200", lazy_t("Default (200 ms)")),
        ("0", lazy_t("0 ms (no delay)")),
        ("100", lazy_t("100 ms")),
        ("300", lazy_t("300 ms")),
        ("500", lazy_t("500 ms")),
    ]
    tts_hover_delay = SelectField(
        lazy_t("Hover pronunciation delay (ms)"),
        choices=tts_hover_delay_choices,
        default="200",
    )
    tts_click_pronunciation = BooleanField(
        lazy_t("Pronounce word when clicked (term form opens)")
    )
    # Master visibility of the reading page's player bar (audio track and
    # TTS alike); the reading menu's "Player" switch writes the same
    # setting, so the name stays readable on both surfaces.
    tts_show_control_panel = BooleanField(lazy_t("Show player"))
    tts_show_sentence_buttons = BooleanField(lazy_t("Show 🔊 button on each sentence"))

    def validate_backup_dir(self, field):
        "Field must be set if enabled."
        if self.backup_enabled.data is False:
            return
        v = field.data
        if (v or "").strip() == "":
            raise ValidationError("Backup directory required")

        abspath = os.path.abspath(v)
        if v != abspath:
            msg = f'Backup dir must be absolute path.  Did you mean "{abspath}"?'
            raise ValidationError(msg)
        if not os.path.exists(v):
            raise ValidationError(f'Directory "{v}" does not exist.')
        if not os.path.isdir(v):
            raise ValidationError(f'"{v}" is not a directory.')


class UserShortcutsForm(FlaskForm):
    """
    Shortcuts form.

    The route manages getting and storing the settings
    from the db, as there's a variable number of settings,
    and it's easier to just work with the data directly
    rather than trying to create a variable number of fields.

    I'm only using this form to get the validate_on_submit()!
    There's likely a better way to do this.
    """
