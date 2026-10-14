"""
UI language translations.

The UI text lives in templates as English source strings, wrapped in
t(...) calls: Jinja renders them via an injected context-processor
function, JS uses I18N.t (window.LUTE_I18N injected by base.html), and
Python code calls t() directly for flash messages and strings built in
routes.  The lookup uses the English source as the key; anything
missing falls back to the English source, so an incomplete dictionary
degrades gracefully.

Strings that are defined before a request exists -- WTForms field
labels, SelectField choices, render_kw titles -- are wrapped in
lazy_t(...) instead, which resolves t() when the value is rendered.

The language is the per-user setting "ui_language" ("en", "zh", "ja",
"ko"; default "en"), stored in the settings table and cached in
lute.settings.current like every other user setting.
"""

import json
import os
import threading

from markupsafe import Markup, escape

# code -> (code, native label shown in the settings dropdown).
UI_LANGUAGES = [
    ("en", "English"),
    ("zh", "中文"),
    ("ja", "日本語"),
    ("ko", "한국어"),
]

_TRANSLATIONS_DIR = os.path.dirname(__file__)

# lang -> {english source: translated string}
_translations = {}
_lock = threading.Lock()


def language_codes():
    "Valid ui_language setting values."
    return [code for code, _label in UI_LANGUAGES]


def current_language():
    "The current scope's ui_language setting, defaulting to 'en'."
    try:
        from lute.settings.current import current_settings

        lang = (current_settings().get("ui_language") or "en").strip()
    except Exception:  # pylint: disable=broad-exception-caught
        # No app context / no db (e.g. rendering outside a request):
        # serve English.
        return "en"
    if lang not in language_codes():
        return "en"
    return lang


def _load(lang):
    "Load and cache the dictionary for the given language."
    if lang in _translations:
        return _translations[lang]
    d = {}
    if lang != "en":
        path = os.path.join(_TRANSLATIONS_DIR, f"{lang}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
    with _lock:
        _translations[lang] = d
    return d


def reload_translations():
    "Drop the cached dictionaries (used after editing the json files)."
    with _lock:
        _translations.clear()


def t(key, lang=None):
    "Translate the English source string, falling back to it verbatim."
    lang = lang or current_language()
    # Markup: keys and dictionary values are developer-authored static
    # text (never user data), and many carry the inline HTML tags the
    # original templates had (<b>, <a href>, &amp;, ...).  Returning
    # Markup keeps those rendering exactly as they did when they were
    # hard-coded in the templates; Jinja would otherwise escape them.
    if lang == "en":
        return Markup(key)
    return Markup(_load(lang).get(key, key))


class _LazyTranslation:
    """
    A translation that resolves when it is rendered, not when it is defined.

    WTForms field labels, SelectField choice lists and render_kw titles
    are built at class-definition time (module import), long before any
    request -- t() there would either fail for lack of an app context or
    freeze the language at import time.  This proxy stores the English
    source key and calls t() from __str__/__html__, which run inside the
    request that renders the form, so the current user's ui_language
    applies.  Outside a request it degrades to English, like t() itself.
    """

    def __init__(self, key):
        self.key = key

    def __str__(self):
        return str(t(self.key))

    def __html__(self):
        # Labels and choice texts are plain text, not markup: escape the
        # resolved translation so it renders as text.
        return escape(str(t(self.key)))

    def __repr__(self):
        return f"_LazyTranslation({self.key!r})"


def lazy_t(key):
    "Wrap an English source string for translation at render time."
    return _LazyTranslation(key)


def translations_for_js(lang=None):
    "The current language's dictionary as a JSON string for the frontend."
    lang = lang or current_language()
    return json.dumps(_load(lang), ensure_ascii=False)
