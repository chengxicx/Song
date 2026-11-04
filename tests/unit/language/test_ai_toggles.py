"""
Per-language AI switches (Language form).

The API key/model are configured once in Settings; these two checkboxes
decide whether the reader's Sentence tab and the Grammar panel offer AI
for this language.  They default to on so existing languages are not
changed by the new columns.
"""

import re

from lute.db import db
from lute.language.forms import LanguageForm
from lute.models.language import Language
from tests.utils import make_book


def test_new_language_defaults_both_ai_features_on(app_context):
    "A language created through the model starts with AI enabled."
    lang = Language()
    lang.name = "AI defaults"
    assert lang.ai_sentence_translation is True
    assert lang.ai_grammar_analysis is True

    form = LanguageForm(obj=lang)
    assert form.ai_sentence_translation.data is True
    assert form.ai_grammar_analysis.data is True


def test_language_form_renders_both_switches_checked_by_default(client, english):
    "The edit form shows the two boxes ticked for an untouched language."
    body = client.get(f"/language/edit/{english.id}").get_data(as_text=True)
    assert 'name="ai_sentence_translation"' in body
    assert 'name="ai_grammar_analysis"' in body
    assert re.search(r'checked[^>]*name="ai_sentence_translation"', body)
    assert re.search(r'checked[^>]*name="ai_grammar_analysis"', body)


def test_populating_the_form_saves_both_switches(client, english):
    "The route saves through populate_obj; unchecked boxes persist as off."
    book = make_book("AI toggles", "This is a sentence.", english)
    db.session.add(book)
    db.session.commit()

    page = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert "/ai/sentence?lang=" in page
    assert "LUTE_AI_GRAMMAR_ENABLED = true" in page

    form = LanguageForm(obj=english)
    form.ai_sentence_translation.data = False
    form.ai_grammar_analysis.data = False
    form.populate_obj(english)
    db.session.commit()

    lang = db.session.get(Language, english.id)
    db.session.refresh(lang)
    assert lang.ai_sentence_translation is False
    assert lang.ai_grammar_analysis is False

    page = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert "/ai/sentence?lang=" not in page
    assert "LUTE_AI_GRAMMAR_ENABLED = false" in page
