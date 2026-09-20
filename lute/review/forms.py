"""
ReviewSpec and review settings forms.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, TextAreaField, IntegerField, FloatField
from wtforms.validators import (
    DataRequired,
    Length,
    InputRequired,
    NumberRange,
    ValidationError,
)

from lute.db import db
from lute.models.review import ReviewSpec
from lute.ankiexport.criteria import validate_criteria
from lute.ankiexport.exceptions import AnkiExportConfigurationError


class ReviewSpecForm(FlaskForm):
    "Review spec: which terms are admitted to the queue, as which cards."

    def __init__(self, *args, spec_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Flask doesn't allow for "general" errors, so handle those specially.
        self.general_errors = []
        # Excluded from the name-uniqueness check when editing.
        self.spec_id = spec_id

    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    criteria = TextAreaField(
        "Criteria",
        render_kw={
            "placeholder": (
                'e.g. status > 1 and language == "Japanese"; '
                "leave blank for all terms"
            )
        },
    )
    card_recognition = BooleanField(
        "Recognition (see the word, recall the meaning)", default=True
    )
    card_recall = BooleanField(
        "Recall, typing (see the meaning, type the word)", default=False
    )
    card_cloze = BooleanField(
        "Cloze (the word is blanked out of a real sentence)", default=True
    )
    active = BooleanField("Active", default=True)

    _TYPE_FIELDS = [
        ("recognition", "card_recognition"),
        ("recall", "card_recall"),
        ("cloze", "card_cloze"),
    ]

    def validate_name(self, field):
        "Names are unique in the db; catch a clash here instead of on commit."
        if not field.data:
            return
        q = db.session.query(ReviewSpec).filter(ReviewSpec.name == field.data)
        if self.spec_id is not None:
            q = q.filter(ReviewSpec.id != self.spec_id)
        if q.first() is not None:
            raise ValidationError("A review spec with that name already exists.")

    def validate(self, extra_validators=None):
        "Also check the criteria string and card type selection."
        ok = super().validate(extra_validators)
        if ok:
            try:
                validate_criteria(self.criteria.data or "")
            except AnkiExportConfigurationError as ex:
                self.criteria.errors = list(self.criteria.errors or []) + [str(ex)]
                ok = False
        if ok and len(self.enabled_card_types()) == 0:
            self.general_errors.append("Enable at least one card type.")
            ok = False
        return ok

    def enabled_card_types(self):
        "Card types whose checkbox is ticked, in canonical order."
        return [ct for ct, fldname in self._TYPE_FIELDS if self[fldname].data]


class ReviewSettingsForm(FlaskForm):
    """
    Review scheduling settings.

    Field names are the keys in the settings table, as with
    lute.settings.forms.UserSettingsForm.
    """

    review_desired_retention = FloatField(
        "Desired retention",
        validators=[InputRequired(), NumberRange(min=0.5, max=0.99)],
        render_kw={
            "type": "number",
            "step": "0.01",
            "min": "0.5",
            "max": "0.99",
            "title": "FSRS target memory retention, 0.5 - 0.99.  "
            "Higher means more frequent reviews.",
        },
    )
    review_max_new_per_day = IntegerField(
        "Max new cards per day",
        validators=[InputRequired(), NumberRange(min=0)],
        render_kw={
            "type": "number",
            "step": "1",
            "min": "0",
            "title": "New cards a day's session can introduce; 0 means no new cards.",
        },
    )
