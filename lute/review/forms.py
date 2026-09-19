"""
ReviewSpec form.
"""

from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, TextAreaField
from wtforms.validators import DataRequired, Length

from lute.ankiexport.criteria import validate_criteria
from lute.ankiexport.exceptions import AnkiExportConfigurationError


class ReviewSpecForm(FlaskForm):
    "Review spec: which terms are admitted to the queue, as which cards."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Flask doesn't allow for "general" errors, so handle those specially.
        self.general_errors = []

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
