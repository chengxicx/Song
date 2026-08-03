"""
Book create/edit forms.
"""

import json
from flask import request
from wtforms import StringField, SelectField, TextAreaField, IntegerField, HiddenField
from wtforms import ValidationError
from wtforms.validators import DataRequired, Length, NumberRange
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed

# Global configuration for allowed audio files
ALLOWED_AUDIO_EXTENSIONS = [
    "mp3",
    "m4a",
    "m4b",
    "wav",
    "ogg",
    "opus",
    "aac",
    "flac",
    "webm",
]
AUDIO_VALIDATION_MSG = (
    f"Please upload a valid audio file ({', '.join(ALLOWED_AUDIO_EXTENSIONS)})"
)


def _tag_values(field_data):
    "Convert field data to array."
    ret = []
    if field_data:
        ret = [h["value"] for h in json.loads(field_data)]
    return ret


class NewBookForm(FlaskForm):
    """
    New book.  All fields can be entered.
    """

    language_id = SelectField("Language", coerce=int)

    title = StringField("Title", validators=[DataRequired(), Length(max=255)])

    desc = (
        "Use for short texts, e.g. up to a few thousand words. "
        + 'For longer texts, use the "Text file" below.'
    )
    text = TextAreaField("Text", description=desc)
    textfile = FileField(
        "Text file",
        validators=[
            FileAllowed(
                ["txt", "epub", "pdf", "srt", "vtt"],
                "Please upload a valid text (txt, epub, pdf, srt, vtt)",
            )
        ],
    )
    split_by = SelectField(
        "Split by", choices=[("paragraphs", "Paragraphs"), ("sentences", "Sentences")]
    )
    threshold_page_tokens = IntegerField(
        "Words per page",
        validators=[NumberRange(min=1, max=1500)],
        default=250,
    )
    source_uri = StringField("Text source", validators=[Length(max=1000)])
    audiofile = FileField(
        "Audio file",
        validators=[
            FileAllowed(
                ALLOWED_AUDIO_EXTENSIONS,
                AUDIO_VALIDATION_MSG,
            )
        ],
    )
    book_tags = StringField("Tags")

    def __init__(self, *args, **kwargs):
        "Call the constructor of the superclass (FlaskForm)"
        super().__init__(*args, **kwargs)
        book = kwargs.get("obj")

        def _data(arr):
            "Get data in proper format for tagify."
            return json.dumps([{"value": p} for p in arr])

        self.book_tags.data = _data(book.book_tags)
        if request.method == "POST":
            self.book_tags.data = request.form.get("book_tags", "")

    def populate_obj(self, obj):
        "Call the populate_obj method from the parent class, then mine."
        super().populate_obj(obj)
        obj.book_tags = _tag_values(self.book_tags.data)
        tfd = self.textfile.data
        if tfd:
            obj.text_stream = tfd.stream
            obj.text_stream_filename = tfd.filename
        afd = self.audiofile.data
        if afd:
            obj.audio_stream = afd.stream
            obj.audio_stream_filename = afd.filename

    def validate_language_id(self, field):  # pylint: disable=unused-argument
        "Language must be set."
        if self.language_id.data in (None, 0):
            raise ValidationError("Please select a language")

    def validate_text(self, field):  # pylint: disable=unused-argument
        "Throw if missing text and textfile, or if have both."
        have_text = self.text.data not in ("", None)
        have_textfile = self.textfile.data not in ("", None)
        if have_text and have_textfile:
            raise ValidationError(
                "Both Text and Text file are set, please only specify one"
            )
        if have_text is False and have_textfile is False:
            raise ValidationError("Please specify either Text or Text file")


class EditBookForm(FlaskForm):
    """
    Edit existing book.  Title, source, tags, audio, and text can be changed.
    """

    title = StringField("Title", validators=[DataRequired(), Length(max=255)])
    text = TextAreaField("Text")
    textfile = FileField(
        "Text file",
        validators=[
            FileAllowed(
                ["txt", "epub", "pdf", "srt", "vtt"],
                "Please upload a valid text (txt, epub, pdf, srt, vtt)",
            )
        ],
    )
    split_by = SelectField(
        "Split by", choices=[("paragraphs", "Paragraphs"), ("sentences", "Sentences")]
    )
    threshold_page_tokens = IntegerField(
        "Words per page",
        validators=[NumberRange(min=1, max=1500)],
        default=250,
    )
    source_uri = StringField("Source URI", validators=[Length(max=1000)])
    book_tags = StringField("Tags")
    audiofile = FileField(
        "Audio file",
        validators=[
            FileAllowed(
                ALLOWED_AUDIO_EXTENSIONS,
                AUDIO_VALIDATION_MSG,
            )
        ],
    )

    # YouTube video book fields.
    book_type = SelectField(
        "Type",
        choices=[("", "Text"), ("youtube", "YouTube video")],
    )
    youtube_srt = FileField(
        "YouTube subtitle file (SRT)",
        validators=[
            FileAllowed(
                ["srt", "vtt"],
                "Please upload a valid subtitle file (srt, vtt)",
            )
        ],
    )

    # The current audio_filename can be removed from the current book.
    audio_filename = HiddenField("Audio filename")

    def __init__(self, *args, **kwargs):
        "Call the constructor of the superclass (FlaskForm)"
        super().__init__(*args, **kwargs)
        book = kwargs.get("obj")

        def _data(arr):
            "Get data in proper format for tagify."
            return json.dumps([{"value": p} for p in arr])

        self.book_tags.data = _data(book.book_tags)
        if request.method == "POST":
            self.book_tags.data = request.form.get("book_tags", "")

    def populate_obj(self, obj):
        "Call the populate_obj method from the parent class, then mine."
        super().populate_obj(obj)
        obj.book_tags = _tag_values(self.book_tags.data)

        # If the type was changed away from youtube, clear the youtube data.
        if obj.book_type != "youtube":
            obj.srt_data = None
            obj.video_current_pos = None

        tfd = self.textfile.data
        if tfd:
            obj.text_stream = tfd.stream
            obj.text_stream_filename = tfd.filename

        afd = self.audiofile.data
        if afd:
            obj.audio_stream = afd.stream
            obj.audio_stream_filename = afd.filename
            obj.audio_bookmarks = None
            obj.audio_current_pos = None

        yfd = self.youtube_srt.data
        if yfd:
            # Re-parse the subtitle file, updating both the book text
            # (for reading) and the cue timing data (for the player).
            from lute.book.service import parse_subtitle_file  # pylint: disable=import-outside-toplevel, cyclic-import
            from lute.db import db  # pylint: disable=import-outside-toplevel
            from lute.models.repositories import (  # pylint: disable=import-outside-toplevel
                LanguageRepository,
            )

            # Load the language so language-specific cue refinement
            # (e.g. Japanese sentence merging/splitting) is applied.
            lang = None
            if getattr(obj, "language_id", None):
                lang = LanguageRepository(db.session).find(obj.language_id)

            text, cues_json = parse_subtitle_file(
                yfd.filename, yfd.stream, language=lang
            )
            obj.text = text
            obj.srt_data = cues_json
            obj.book_type = "youtube"
