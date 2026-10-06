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
    review_max_shadowing_per_day = IntegerField(
        "Max shadowing cards per day",
        validators=[InputRequired(), NumberRange(min=0)],
        render_kw={
            "type": "number",
            "step": "1",
            "min": "0",
            "title": "Shadowing cards a day's session can serve, due or new; "
            "0 turns them off without disabling the card type.",
        },
    )
    review_speak_cards = BooleanField(
        "Speak each card's answer",
        default=True,
        description="Pronounce the term with text-to-speech when a card's "
        "answer is revealed, never on the front -- so the word is not read "
        "out before you have recalled it.  A shadowing card also plays its "
        "sentence when it opens, because that is the model audio.  The "
        "card's speaker button works either way.",
    )
    card_recognition = BooleanField(
        "Recognition (see the word, recall the meaning)", default=True
    )
    card_cloze = BooleanField(
        "Cloze (the word is blanked out of a real sentence)", default=True
    )
    card_shadowing = BooleanField(
        "Shadowing (hear a real sentence, read it aloud, get it scored)",
        default=False,
    )
