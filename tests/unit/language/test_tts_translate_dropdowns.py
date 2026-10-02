"""
Tests for the language form TTS / translate target dropdowns.
"""

from lute.language.langtags import LANGUAGE_TAGS, tag_choices
from lute.language.forms import LanguageForm


def test_language_tags_are_unique_and_include_key_tags():
    "The tag list is clean and covers the Chinese variants."
    tags = [t for t, _ in LANGUAGE_TAGS]
    assert len(tags) == len(set(tags)), "no duplicate tags"
    for expected in ("zh-CN", "zh-HK", "zh-TW", "en-US", "ja-JP"):
        assert expected in tags, expected


def test_tag_choices_start_with_default_option():
    "The first choice is the empty Default option describing the fallback."
    choices = tag_choices("browser language")
    assert choices[0] == ("", "Default (browser language)")
    # All tags follow, rendered as "tag -- description".
    assert choices[1][0] == LANGUAGE_TAGS[0][0]
    assert "--" in choices[1][1]


def test_form_fields_are_dropdowns_with_default_choice(app_context):
    "Both fields are selects whose first option is the Default."
    form = LanguageForm()
    assert form.tts_lang.choices[0][0] == ""
    assert form.tts_lang.choices[0][1] == "Default (auto-detect from language name)"
    assert "zh-HK" in [c[0] for c in form.tts_lang.choices]

    assert form.translate_target_lang.choices[0][0] == ""
    assert form.translate_target_lang.choices[0][1] == "Default (browser language)"
    assert "zh-CN" in [c[0] for c in form.translate_target_lang.choices]


def test_stored_custom_tag_is_kept_selectable(app_context):
    "A stored value outside the standard list (e.g. yue) is appended."
    from lute.db import db
    from lute.models.language import Language
    from lute.language.routes import _ensure_tag_choices_include_current

    lang = Language()
    lang.name = "Tagdrop Test Lang"
    lang.tts_lang = "yue"
    db.session.add(lang)
    db.session.commit()

    form = LanguageForm(obj=lang)
    _ensure_tag_choices_include_current(form, lang)

    tts_choices = [c[0] for c in form.tts_lang.choices]
    assert "yue" in tts_choices, "custom stored value appended"
    assert tts_choices.count("yue") == 1, "appended exactly once"
    # The standard list is untouched (shared class-level choices).
    std = LanguageForm()
    assert "yue" not in [c[0] for c in std.tts_lang.choices]


def test_form_post_persists_selected_tags(client):
    "Posting valid tags saves them to the language."
    from lute.db import db
    from lute.models.language import Language

    with client.application.app_context():
        lang = Language()
        lang.name = "Tagdrop Post Lang"
        db.session.add(lang)
        db.session.commit()
        langid = lang.id

    resp = client.post(
        f"/language/edit/{langid}",
        data={
            "name": "Tagdrop Post Lang",
            "parser_type": "spacedel",
            "dictionaries-0-usefor": "terms",
            "dictionaries-0-dicttype": "popuphtml",
            "dictionaries-0-dicturi": "https://example.com/[LUTE]",
            "dictionaries-0-is_active": "y",
            "dictionaries-0-sort_order": "1",
            "dictionaries-1-usefor": "sentences",
            "dictionaries-1-dicttype": "popuphtml",
            "dictionaries-1-dicturi": "https://example.net/[LUTE]",
            "dictionaries-1-is_active": "y",
            "dictionaries-1-sort_order": "2",
            "tts_lang": "zh-HK",
            "translate_target_lang": "zh-CN",
            "grammar_translate_lang": "en",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, "successful save redirects"

    with client.application.app_context():
        lang = db.session.get(Language, langid)
        assert lang.tts_lang == "zh-HK"
        assert lang.translate_target_lang == "zh-CN"


# ---- hidden grammar groups --------------------------------------------


def _japanese_like_language():
    from lute.models.language import Language

    lang = Language()
    lang.name = "Japanese"
    lang.parser_type = "japanese_sudachi"
    return lang


def test_hidden_grammar_choices_depend_on_the_language(app_context):
    "Japanese offers JLPT plus the aggregates; no-engine languages offer none."
    from lute.language.forms import LanguageForm
    from lute.language.routes import _set_hidden_grammar_choices
    from lute.models.language import Language

    japanese = _japanese_like_language()
    form = LanguageForm(obj=japanese)
    _set_hidden_grammar_choices(form, japanese)
    tokens = [c[0] for c in form.hidden_grammar_levels.choices]
    assert tokens[:5] == ["N5", "N4", "N3", "N2", "N1"]
    assert tokens[5:] == ["basic_forms", "basic_particles"]

    turkish = Language()
    turkish.name = "Turkish"
    form2 = LanguageForm(obj=turkish)
    _set_hidden_grammar_choices(form2, turkish)
    assert form2.hidden_grammar_levels.choices == []


def test_stored_unknown_hidden_token_stays_selectable(app_context):
    "A stale token is still offered so the user can see and clear it."
    from lute.language.forms import LanguageForm
    from lute.language.routes import _set_hidden_grammar_choices

    lang = _japanese_like_language()
    lang.hidden_grammar_levels = ["N5", "N9-retired"]
    form = LanguageForm(obj=lang)
    _set_hidden_grammar_choices(form, lang)

    choices = dict(form.hidden_grammar_levels.choices)
    assert choices["N9-retired"].endswith("(current setting)")
    assert form.hidden_grammar_levels.data == ["N5", "N9-retired"]
    # The shared class-level default is untouched.
    assert LanguageForm().hidden_grammar_levels.choices == []


def test_form_post_persists_hidden_grammar_levels(client, empty_db, japanese):
    "Posting the checked boxes saves them to the language."
    from lute.db import db
    from lute.models.language import Language

    langid = japanese.id
    data = {
        "name": japanese.name,
        "parser_type": japanese.parser_type,
        # The tag dropdowns are selects, and a SelectField whose value is
        # absent falls back to the object's (None) and fails validation.
        "tts_lang": "",
        "translate_target_lang": "",
        "grammar_translate_lang": "en",
        "hidden_grammar_levels": ["N5", "basic_forms"],
    }
    for i, d in enumerate(japanese.dictionaries):
        data[f"dictionaries-{i}-usefor"] = d.usefor
        data[f"dictionaries-{i}-dicttype"] = d.dicttype
        data[f"dictionaries-{i}-dicturi"] = d.dicturi
        data[f"dictionaries-{i}-is_active"] = "y"
        data[f"dictionaries-{i}-sort_order"] = str(i + 1)

    resp = client.post(f"/language/edit/{langid}", data=data, follow_redirects=False)
    assert resp.status_code == 302, "successful save redirects"

    with client.application.app_context():
        lang = db.session.get(Language, langid)
        assert lang.hidden_grammar_levels == ["N5", "basic_forms"]


def test_form_post_with_no_boxes_checked_clears_the_setting(client, empty_db, japanese):
    "Omitting the field (nothing checked) hides nothing again."
    from lute.db import db
    from lute.models.language import Language

    japanese.hidden_grammar_levels = ["N5"]
    db.session.add(japanese)
    db.session.commit()
    langid = japanese.id

    data = {
        "name": japanese.name,
        "parser_type": japanese.parser_type,
        "tts_lang": "",
        "translate_target_lang": "",
        "grammar_translate_lang": "en",
    }
    for i, d in enumerate(japanese.dictionaries):
        data[f"dictionaries-{i}-usefor"] = d.usefor
        data[f"dictionaries-{i}-dicttype"] = d.dicttype
        data[f"dictionaries-{i}-dicturi"] = d.dicturi
        data[f"dictionaries-{i}-is_active"] = "y"
        data[f"dictionaries-{i}-sort_order"] = str(i + 1)

    resp = client.post(f"/language/edit/{langid}", data=data, follow_redirects=False)
    assert resp.status_code == 302, "successful save redirects"

    with client.application.app_context():
        lang = db.session.get(Language, langid)
        assert lang.hidden_grammar_levels == []
