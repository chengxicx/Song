"""
Review settings forms.
"""

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, FloatField
from wtforms.validators import InputRequired, NumberRange


class ReviewSettingsForm(FlaskForm):
    """
    Review scheduling settings and card types.

    Field names are the keys in the settings table, as with
    lute.settings.forms.UserSettingsForm -- except the card-type
    checkboxes, which the route serializes into the single
    review_card_types setting.
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
    card_recognition = BooleanField(
        "Recognition (see the word, recall the meaning)", default=True
    )
    card_cloze = BooleanField(
        "Cloze (the word is blanked out of a real sentence)", default=True
    )
