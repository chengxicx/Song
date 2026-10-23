"""
The text and engine plumbing shared by the reading page's grammar panel
and the whole-book precompute worker.

Both callers must feed the engines the exact same text and get the exact
same JSON: the precomputed result is served verbatim, so any drift here --
a different page join, a skipped NFC pass, a different engine choice, a
missing regex fallback -- would surface as a panel that disagrees with the
same book analysed on demand.  Keeping the three pieces in one place is
the only way that promise stays true.
"""

import glob
import hashlib
import os
import threading
import unicodedata

from flask import current_app

from lute.db import db
from lute.read.render.grammar_analysis import analyze as analyze_grammar
from lute.read.render.grammar_analysis import (
    is_arabic_language,
    is_cantonese_language,
    is_english_language,
    is_french_language,
    is_german_language,
    is_italian_language,
    is_japanese_language,
    is_korean_language,
    is_mandarin_chinese_language,
    is_portuguese_language,
    is_russian_language,
    is_spanish_language,
    is_thai_language,
)
from lute.read.render.grammar_analysis_ja import analyze_japanese
from lute.read.render.grammar_analysis_ko import analyze_korean
from lute.read.render.grammar_analysis_zh import analyze_chinese
from lute.read.render.grammar_analysis_yue import analyze_cantonese
from lute.read.render.grammar_analysis_en import analyze_english
from lute.read.render.grammar_analysis_es import analyze_spanish
from lute.read.render.grammar_analysis_ru import analyze_russian
from lute.read.render.grammar_analysis_fr import analyze_french
from lute.read.render.grammar_analysis_de import analyze_german
from lute.read.render.grammar_analysis_it import analyze_italian
from lute.read.render.grammar_analysis_pt import analyze_portuguese
from lute.read.render.grammar_analysis_th import analyze_thai
from lute.read.render.grammar_analysis_ar import analyze_arabic
from lute.read.render.service import Service as RenderService

# Detector -> engine.  Every engine runs on optional heavy dependencies
# (Sudachi, Kiwi, spaCy models, pymorphy3, pythainlp, pyarabic); when one
# is missing the caller falls back to the generic regex rule library
# instead of failing the panel.
_ENGINES = (
    (is_japanese_language, analyze_japanese, "japanese-sudachi"),
    (is_korean_language, analyze_korean, "korean"),
    (is_mandarin_chinese_language, analyze_chinese, "chinese"),
    (is_cantonese_language, analyze_cantonese, "cantonese"),
    (is_english_language, analyze_english, "english"),
    (is_spanish_language, analyze_spanish, "spanish"),
    (is_russian_language, analyze_russian, "russian"),
    (is_french_language, analyze_french, "french"),
    (is_german_language, analyze_german, "german"),
    (is_italian_language, analyze_italian, "italian"),
    (is_portuguese_language, analyze_portuguese, "portuguese"),
    (is_thai_language, analyze_thai, "thai"),
    (is_arabic_language, analyze_arabic, "arabic"),
)


def clean_grammar_text(text):
    """
    Strip the reader's display artifacts from text about to be analysed.

    The reader renders empty paragraphs as a zero-width-space placeholder
    and can inject the speaker marker into the text the client sends back;
    both are display artifacts, not grammar.  An imported book may also
    store decomposed Hangul (e.g. a leading syllable + U+11AF where the
    source had one syllable): an engine tokenising that reports offsets
    inside a syllable, and the client -- which slices the sentence at those
    offsets -- renders half a character.  Composing once here keeps every
    engine's tokens, offsets and echoed sentences consistent.
    """
    text = (text or "").replace("\u200b", "").replace("🔊", "")
    return unicodedata.normalize("NFC", text)


def book_grammar_text(book):
    """
    The whole-book analysis text: every page joined by a newline, so a page
    boundary never glues two sentences into one pseudo-sentence.
    """
    return clean_grammar_text("\n".join(t.text or "" for t in book.texts))


def text_hash(text):
    "Identity of an analysed text: an edited page changes it."
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def grammar_engine_for(lang):
    """
    ``(engine, extra_name)`` for this language, or ``(None, None)`` when no
    dedicated engine claims it.
    """
    for detector, engine, extra in _ENGINES:
        if detector(lang):
            return engine, extra
    return None, None


def display_lang_for(lang):
    "The language the entry descriptions are translated into."
    return getattr(lang, "grammar_translate_lang", "") or "en"


def analyze_text(text, lang):
    """
    Run the language's grammar engine over ``text``.

    Returns the engine's RAW output -- the language's hidden_grammar_levels
    filter is the caller's job.  Hiding a level is a display choice: the
    whole-book cache stores unfiltered rows so toggling it costs nothing.
    """
    display = display_lang_for(lang)
    engine, extra = grammar_engine_for(lang)
    if engine is not None:
        try:
            return engine(text, display_lang=display)
        except (ImportError, OSError):
            current_app.logger.warning(
                "%s grammar engine not installed; using the basic regex rules. "
                'Run: pip install -e ".[%s]"',
                extra.capitalize(),
                extra,
            )
    render_service = RenderService(db.session)
    paragraphs = render_service.get_paragraphs(text, lang)
    sentences = [
        "".join(ti.text for ti in sentence) for para in paragraphs for sentence in para
    ]
    return analyze_grammar(sentences)


# The rule library lives in code (hand-written rules inside the engine
# modules) and in lute/jlpt_data/grammar/*.json.  A deploy that changes
# either makes every cached analysis wrong, so the cache signature carries
# a fingerprint of those bytes -- no manual table wipe after a rule change.
_FINGERPRINT = None
_FINGERPRINT_LOCK = threading.Lock()


def _rule_source_files():
    here = os.path.dirname(os.path.abspath(__file__))
    render = os.path.join(here, "render")
    files = sorted(glob.glob(os.path.join(render, "grammar_analysis*.py")))
    jlpt = os.path.join(os.path.dirname(here), "jlpt_data")
    files += sorted(glob.glob(os.path.join(jlpt, "grammar", "*.json")))
    files += sorted(glob.glob(os.path.join(jlpt, "grammar_*.json")))
    return files


def rules_fingerprint():
    "Stable hash of the grammar rule sources, computed once per process."
    global _FINGERPRINT
    if _FINGERPRINT is None:
        with _FINGERPRINT_LOCK:
            if _FINGERPRINT is None:
                digest = hashlib.sha1()
                for path in _rule_source_files():
                    digest.update(os.path.basename(path).encode("utf-8"))
                    try:
                        with open(path, "rb") as f:
                            for chunk in iter(lambda: f.read(1 << 20), b""):
                                digest.update(chunk)
                    except OSError:  # pragma: no cover - file vanished mid-scan
                        digest.update(b"missing")
                _FINGERPRINT = digest.hexdigest()
    return _FINGERPRINT
