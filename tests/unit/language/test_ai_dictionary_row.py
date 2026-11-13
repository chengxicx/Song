"""
The AI sentence tab as an ordinary, sortable dictionary row.

The reader used to prepend a hardcoded AI tab in lute/read/routes.py, so
the AI tab could not be seen or ordered in the Language form.  It is now
a 'languagedicts' row of type AI: the row's position is the tab's
position, like every other tab.  The per-language "AI sentence
translation" switch stays the master gate.
"""

import ast
import os
import re

from lute.db import db
from lute.models.language import AI_DICT_TYPE, Language, LanguageDictionary
from tests.utils import make_book

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATION = os.path.join(
    HERE,
    "..",
    "..",
    "..",
    "lute",
    "db",
    "schema",
    "migrations",
    "20261105_add_language_ai_dictionary_row.sql",
)


def _row(usefor, dtype, uri, order):
    d = LanguageDictionary()
    d.usefor = usefor
    d.dicttype = dtype
    d.dicturi = uri
    d.is_active = True
    d.is_hidden = False
    d.sort_order = order
    return d


def _ai_row(lang, order):
    return _row("sentences", AI_DICT_TYPE, _ai_url(lang), order)


def _ai_url(lang):
    return f"/ai/sentence?lang={lang.id}&q=[LUTE]"


def _sentence_row(uri, order):
    return _row("sentences", "embeddedhtml", uri, order)


def _terms_row(order):
    "Every language form needs one active Terms dictionary."
    return _row("terms", "embeddedhtml", "https://term.example.com/[LUTE]", order)


def _language(name, rows, ai_sentence=True):
    lang = Language()
    lang.name = name
    lang.parser_type = "spacedel"
    lang.dictionaries = list(rows)
    lang.ai_sentence_translation = ai_sentence
    db.session.add(lang)
    db.session.commit()
    return lang


def _append(lang, row):
    lang.dictionaries.append(row)
    db.session.add(lang)
    db.session.commit()


def _reload(lang):
    db.session.expire_all()
    return db.session.get(Language, lang.id)


def _sentence_urls(lang):
    return [d["url"] for d in lang.sentence_dict_uris]


def _form_data(lang, rows, ai_sentence=True, **overrides):
    """
    The language form's minimum, with one dictionary row per tuple:
    (usefor, dicttype, dicturi, sort_order, is_active).
    """
    data = {
        "name": lang.name,
        "parser_type": lang.parser_type,
        "character_substitutions": lang.character_substitutions,
        "regexp_split_sentences": lang.regexp_split_sentences,
        "exceptions_split_sentences": lang.exceptions_split_sentences,
        "word_characters": lang.word_characters,
        "tts_lang": "",
        "translate_target_lang": "",
        "grammar_translate_lang": "en",
    }
    if ai_sentence:
        data["ai_sentence_translation"] = "y"
    for i, (usefor, dtype, uri, order, active) in enumerate(rows):
        data[f"dictionaries-{i}-usefor"] = usefor
        data[f"dictionaries-{i}-dicttype"] = dtype
        data[f"dictionaries-{i}-dicturi"] = uri
        data[f"dictionaries-{i}-sort_order"] = str(order)
        if active:
            data[f"dictionaries-{i}-is_active"] = "y"
    data.update(overrides)
    return data


# --- The tab list ------------------------------------------------------------


def test_ai_tab_position_is_the_row_position(app_context, empty_db):
    "First, middle and last: the AI tab lands wherever the row sorts."
    urls = ["https://s1.example.com/[LUTE]", "https://s2.example.com/[LUTE]"]
    for ai_order in (1, 2, 3):
        lang = _language(f"AI order {ai_order}", [])
        rows = [_sentence_row(urls[0], 1), _sentence_row(urls[1], 2)]
        rows.insert(ai_order - 1, _ai_row(lang, ai_order))
        for i, row in enumerate(rows, start=1):
            row.sort_order = i
        _append(lang, rows[0])
        for row in rows[1:]:
            _append(lang, row)

        tabs = lang.sentence_dict_uris
        ai = tabs[ai_order - 1]
        assert ai["url"] == _ai_url(lang)
        assert ai["label"] == "AI"
        assert ai["title"] == "AI sentence translation"
        assert ai["dicttype"] == "embeddedhtml"
        assert [t["url"] for t in tabs].index(_ai_url(lang)) == ai_order - 1


def test_switch_off_hides_the_ai_tab(app_context, empty_db):
    "The switch is the master gate: the row keeps its place, no tab."
    lang = _language("AI switch off", [])
    lang.dictionaries = [
        _ai_row(lang, 1),
        _sentence_row("https://s.example.com/[LUTE]", 2),
    ]
    lang.ai_sentence_translation = False
    db.session.add(lang)
    db.session.commit()

    assert _ai_url(lang) not in _sentence_urls(lang)
    assert len(_sentence_urls(lang)) == 1


def test_no_ai_row_means_no_ai_tab(app_context, empty_db):
    lang = _language("AI row missing", [])
    _append(lang, _sentence_row("https://s.example.com/[LUTE]", 1))

    assert _ai_url(lang) not in _sentence_urls(lang)


def test_reading_page_tab_json_uses_the_rows_order(client, app_context, empty_db):
    "The reader builds its Sentence strip from Language.sentence_dict_uris."
    lang = _language("AI reader order", [])
    s1 = _sentence_row("https://s1.example.com/[LUTE]", 1)
    ai = _ai_row(lang, 2)
    s2 = _sentence_row("https://s2.example.com/[LUTE]", 3)
    for row in (s1, ai, s2):
        _append(lang, row)

    book = make_book("AI tab order", "This is a test.", lang)
    db.session.add(book)
    db.session.commit()

    page = client.get(f"/read/{book.id}").get_data(as_text=True)
    line = next(ln for ln in page.splitlines() if "LUTE_SENTENCE_LOOKUP_DICTS" in ln)
    literal = line.split("=", 1)[1].strip().rstrip(";")
    tabs = ast.literal_eval(literal)

    assert [t["url"] for t in tabs] == [
        "https://s1.example.com/[LUTE]",
        _ai_url(lang),
        "https://s2.example.com/[LUTE]",
    ]


# --- The Language form -------------------------------------------------------


def _ai_block(html):
    "The rendered dictionary row that carries the AI hidden value."
    blocks = html.split('<div class="dict_entry')
    return next(b for b in blocks if 'value="AI"' in b)


def _dictionary_block(html, uri):
    "The rendered row that carries one URL."
    blocks = html.split('<div class="dict_entry')
    return next(b for b in blocks if f'value="{uri}"' in b)


def _uri_control(block):
    "The tag rendered in a row's URL (second) column."
    match = re.search(r"<(?:input|select)[^>]*class=\"dict-uri[^\"]*\"[^>]*>", block)
    assert match is not None, f"no URL control in block: {block[:200]}"
    return match.group(0)


def _configure_provider(provider, token, model=None):
    "Give one provider a key (and model) in Settings, as the settings page does."
    from lute.ai.service import save_grammar_config

    save_grammar_config(provider, token=token, model=model)


def test_the_edit_form_shows_the_ai_row_with_a_drag_handle(
    client, app_context, empty_db
):
    lang = _language("AI form row", [])
    _append(lang, _sentence_row("https://s.example.com/[LUTE]", 1))
    _append(lang, _ai_row(lang, 2))

    html = client.get(f"/language/edit/{lang.id}").get_data(as_text=True)
    block = _ai_block(html)

    assert "drag-handle" in block
    assert "dict-entry" not in block, "sanity: the split starts with the block"
    assert 'value="sentences"' in block, "locked Use-for"
    assert 'value="AI"' in block, "locked type"
    assert "disabled" in block, "the type dropdown is disabled"
    assert "remove-dict-entity" not in block, "the AI row cannot be removed"
    assert "test-dict-entity" not in block
    assert 'type="text"' not in block, "no editable URL for the AI row"


def test_the_ai_rows_provider_select_uses_the_url_column(client, app_context, empty_db):
    """
    The AI row has no URL input, so its provider select stands in that
    cell with the same classes: the two left dropdowns then line up
    with every ordinary row instead of shifting left.
    """
    _configure_provider("openai", "sk-openai-test")
    _configure_provider("anthropic", "sk-ant-test")

    lang = _language("AI provider layout", [])
    sentence = _sentence_row("https://s.example.com/[LUTE]", 1)
    _append(lang, sentence)
    _append(lang, _ai_row(lang, 2))

    html = client.get(f"/language/edit/{lang.id}").get_data(as_text=True)
    ai_block = _ai_block(html)
    plain_block = _dictionary_block(html, "https://s.example.com/[LUTE]")

    ai_control = _uri_control(ai_block)
    plain_control = _uri_control(plain_block)
    assert ai_control.startswith("<select"), ai_control
    assert "dictionaries-" in ai_control and "-ai_provider" in ai_control
    assert 'class="dict-uri form-control-narrower"' in ai_control
    assert 'class="dict-uri form-control-narrower"' in plain_control

    # The row keeps the same operations slot as ordinary rows, but with
    # no test/remove buttons in it, so every flex child adds up to the
    # same width and the columns line up.
    assert ai_block.count('class="dict-ops-slot"') == 2
    assert "test-dict-entity" not in ai_block
    assert "remove-dict-entity" not in ai_block

    # Both provider choices are offered, labelled with their model, and
    # Default keeps the old behaviour for every existing language.
    assert "Default (use the enabled provider in Settings)" in ai_block
    assert "OpenAI · gpt-4o-mini" in ai_block
    assert "Anthropic · claude-sonnet-4-5" in ai_block


def test_saving_the_ai_rows_provider_choice_is_read_back(client, app_context, empty_db):
    _configure_provider("anthropic", "sk-ant-test")
    lang = _language("AI provider save", [])
    _append(lang, _terms_row(1))
    _append(lang, _sentence_row("https://s.example.com/[LUTE]", 2))
    _append(lang, _ai_row(lang, 3))

    rows = [
        ("terms", "embeddedhtml", "https://term.example.com/[LUTE]", 1, True),
        ("sentences", "embeddedhtml", "https://s.example.com/[LUTE]", 2, True),
        ("sentences", "AI", _ai_url(lang), 3, True),
    ]
    data = _form_data(lang, rows, **{"dictionaries-2-ai_provider": "anthropic"})
    response = client.post(f"/language/edit/{lang.id}", data=data)
    assert response.status_code == 302

    reloaded = _reload(lang)
    ai = next(d for d in reloaded.dictionaries if d.dicttype == AI_DICT_TYPE)
    assert ai.ai_provider == "anthropic"
    assert reloaded.ai_sentence_provider == "anthropic"
    assert _ai_url(reloaded) in _sentence_urls(reloaded)


def test_a_provider_without_a_key_or_on_another_row_is_a_form_error(
    client, app_context, empty_db
):
    "Only a configured provider, pinned on the AI row, is accepted."
    _configure_provider("openai", "sk-openai-test")

    lang = _language("AI provider invalid", [])
    _append(lang, _terms_row(1))
    _append(lang, _sentence_row("https://s.example.com/[LUTE]", 2))
    _append(lang, _ai_row(lang, 3))

    rows = [
        ("terms", "embeddedhtml", "https://term.example.com/[LUTE]", 1, True),
        ("sentences", "embeddedhtml", "https://s.example.com/[LUTE]", 2, True),
        ("sentences", "AI", _ai_url(lang), 3, True),
    ]
    # anthropic has no key, gemini is not a provider at all.
    for value in ("anthropic", "gemini"):
        data = _form_data(lang, rows, **{"dictionaries-2-ai_provider": value})
        response = client.post(f"/language/edit/{lang.id}", data=data)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert value in body
        assert "is not an AI provider configured in Settings." in body

    # A configured provider still cannot be pinned on an ordinary row.
    data = _form_data(lang, rows, **{"dictionaries-1-ai_provider": "openai"})
    response = client.post(f"/language/edit/{lang.id}", data=data)
    assert response.status_code == 200
    assert "is not an AI provider configured in Settings." in (
        response.get_data(as_text=True)
    )

    reloaded = _reload(lang)
    ai = next(d for d in reloaded.dictionaries if d.dicttype == AI_DICT_TYPE)
    assert ai.ai_provider == ""


def test_the_form_says_so_when_the_switch_is_off(client, app_context, empty_db):
    lang = _language("AI form off", [], ai_sentence=False)
    _append(lang, _ai_row(lang, 1))

    html = client.get(f"/language/edit/{lang.id}").get_data(as_text=True)
    assert "The AI switch above is off" in html


def test_saving_a_reordered_ai_row_is_read_back(client, app_context, empty_db):
    lang = _language("AI sort save", [])
    _append(lang, _terms_row(1))
    _append(lang, _sentence_row("https://s1.example.com/[LUTE]", 2))
    _append(lang, _sentence_row("https://s2.example.com/[LUTE]", 3))
    _append(lang, _ai_row(lang, 4))

    rows = [
        ("terms", "embeddedhtml", "https://term.example.com/[LUTE]", 1, True),
        ("sentences", "embeddedhtml", "https://s1.example.com/[LUTE]", 2, True),
        ("sentences", "embeddedhtml", "https://s2.example.com/[LUTE]", 4, True),
        ("sentences", "AI", _ai_url(lang), 3, True),
    ]
    response = client.post(f"/language/edit/{lang.id}", data=_form_data(lang, rows))
    assert response.status_code == 302

    reloaded = _reload(lang)
    assert _ai_url(reloaded) in _sentence_urls(reloaded)
    assert _sentence_urls(reloaded) == [
        "https://s1.example.com/[LUTE]",
        _ai_url(reloaded),
        "https://s2.example.com/[LUTE]",
    ]
    ai = next(d for d in reloaded.dictionaries if d.dicttype == AI_DICT_TYPE)
    assert ai.sort_order == 3


def test_the_ai_rows_type_cannot_be_posted_as_another_type(
    client, app_context, empty_db
):
    "The type is locked: a crafted POST cannot turn the AI row into a web tab."
    lang = _language("AI type lock", [])
    _append(lang, _terms_row(1))
    _append(lang, _sentence_row("https://s.example.com/[LUTE]", 2))
    _append(lang, _ai_row(lang, 3))

    rows = [
        ("terms", "embeddedhtml", "https://term.example.com/[LUTE]", 1, True),
        ("sentences", "embeddedhtml", "https://s.example.com/[LUTE]", 2, True),
        ("sentences", "embeddedhtml", _ai_url(lang), 3, True),
    ]
    response = client.post(f"/language/edit/{lang.id}", data=_form_data(lang, rows))

    assert response.status_code == 200
    assert "Not a valid choice" in response.get_data(as_text=True)
    assert _ai_url(_reload(lang)) in _sentence_urls(_reload(lang))


def test_turning_the_switch_on_adds_the_ai_row_at_the_top(
    client, app_context, empty_db
):
    lang = _language("AI switch on", [])
    _append(lang, _terms_row(1))
    _append(lang, _sentence_row("https://s.example.com/[LUTE]", 2))

    data = _form_data(
        lang,
        [
            ("terms", "embeddedhtml", "https://term.example.com/[LUTE]", 1, True),
            ("sentences", "embeddedhtml", "https://s.example.com/[LUTE]", 2, True),
        ],
    )
    client.post(f"/language/edit/{lang.id}", data=data)

    reloaded = _reload(lang)
    ai_rows = [d for d in reloaded.dictionaries if d.dicttype == AI_DICT_TYPE]
    assert len(ai_rows) == 1
    assert ai_rows[0].sort_order == 0
    assert _sentence_urls(reloaded)[0] == _ai_url(reloaded)


def test_turning_the_switch_off_keeps_the_row_and_its_position(
    client, app_context, empty_db
):
    lang = _language("AI switch off keeps row", [])
    _append(lang, _terms_row(1))
    _append(lang, _sentence_row("https://s1.example.com/[LUTE]", 2))
    _append(lang, _ai_row(lang, 3))
    _append(lang, _sentence_row("https://s2.example.com/[LUTE]", 4))

    rows = [
        ("terms", "embeddedhtml", "https://term.example.com/[LUTE]", 1, True),
        ("sentences", "embeddedhtml", "https://s1.example.com/[LUTE]", 2, True),
        ("sentences", "AI", _ai_url(lang), 3, True),
        ("sentences", "embeddedhtml", "https://s2.example.com/[LUTE]", 4, True),
    ]
    client.post(
        f"/language/edit/{lang.id}", data=_form_data(lang, rows, ai_sentence=False)
    )

    reloaded = _reload(lang)
    ai = next(d for d in reloaded.dictionaries if d.dicttype == AI_DICT_TYPE)
    assert ai.sort_order == 3
    assert _ai_url(reloaded) not in _sentence_urls(reloaded)


# --- Migration ----------------------------------------------------------------


def test_migration_inserts_the_ai_row_at_index_zero(app_context, empty_db):
    """
    Languages with the switch on get one AI row before everything else;
    languages with it off are untouched.
    """
    on = _language(
        "Migration AI on",
        [
            _sentence_row("https://s1.example.com/[LUTE]", 1),
            _sentence_row("https://s2.example.com/[LUTE]", 2),
        ],
    )
    off = _language(
        "Migration AI off",
        [
            _sentence_row("https://s1.example.com/[LUTE]", 1),
            _sentence_row("https://s2.example.com/[LUTE]", 2),
        ],
        ai_sentence=False,
    )

    with open(MIGRATION, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = db.engine.raw_connection()
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
    db.session.expire_all()

    on = db.session.get(Language, on.id)
    ai_rows = [d for d in on.dictionaries if d.dicttype == AI_DICT_TYPE]
    assert len(ai_rows) == 1
    assert ai_rows[0].sort_order == 0
    assert ai_rows[0].usefor == "sentences"
    assert ai_rows[0].dicturi == _ai_url(on)
    assert sorted(d.sort_order for d in on.dictionaries) == [0, 2, 3]
    assert _sentence_urls(on)[0] == _ai_url(on)

    off = db.session.get(Language, off.id)
    assert not [d for d in off.dictionaries if d.dicttype == AI_DICT_TYPE]
    assert sorted(d.sort_order for d in off.dictionaries) == [1, 2]
