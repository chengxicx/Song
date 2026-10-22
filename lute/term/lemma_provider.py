"""
Where a term's dictionary form (lemma) comes from, per language.

The parent backfill (``lute/cli/term_parent_backfill.py``) needs two
callables -- ``lemma_of(text)`` and ``content_token_count(text)`` -- and
they cannot both come from the parser for most languages:

* ``AbstractParser.get_lemma(text)`` takes no language, and one
  ``SpaceDelimitedParser`` instance serves every space-delimited language,
  so the parser has no way to know which engine to ask.
* ``content_token_count()`` is implemented by the Sudachi parser only.

So the resolution lives here, keyed by ``Language``, and the CLI asks this
module instead of reaching into the parser.  Three sources, tried in
order:

1. **The parser itself**, when it overrides ``get_lemma()`` *and* provides
   ``content_token_count()``.  That is Japanese (Sudachi) today.  Korean
   overrides ``get_lemma()`` but has no ``content_token_count()``, so it
   is refused exactly as it was before -- see "Known gaps" below.
2. **A spaCy pipeline.**  The grammar panel already loads one per
   language (``lute/read/render/grammar_analysis_matcher.py``) and its
   tokens already carry ``token.lemma_``; the same six languages the
   grammar engines serve (en/es/fr/de/it/pt) get their dictionary form
   for free.
3. **pymorphy3 for Russian**, whose grammar engine already reports
   ``normal_form``.

No new dependency: every source here is an optional extra that
``pyproject.toml`` already declares for the grammar panel, so a machine
with the extra installed gains the backfill for that language and a
machine without it gets a clear refusal instead of a half-working run.

What this deliberately does *not* do is touch the reading page.  Parent
links are a separate axis from the token -> term mapping
(``words.WoTextLC``), and ``get_lemma()`` re-tokenizes an isolated string
with no sentence context, so this belongs in an offline, dry-run-first
tool rather than on the page render path.

Known gaps: Korean (needs ``content_token_count()`` in the lute-korean
plugin, a separately published package) and every language with neither a
spaCy grammar engine nor pymorphy3 (Thai, Arabic, Chinese, Cantonese, and
the ~60 languages that only have a language definition).  For those,
``lemma_provider_for()`` returns None and the CLI refuses.
"""

import importlib.util
import re
from dataclasses import dataclass
from typing import Callable, Optional

from lute.parse.base import AbstractParser
from lute.term.lemma_parents import ZWS

# Space-delimited text splits into words on any whitespace run.  Used as
# the content-word count for every source that does not do its own
# morphological counting -- see content_token_count below.
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class LemmaProvider:
    """
    One language's dictionary-form source.

    ``label`` is what the backfill report shows, so a dry run says which
    engine produced the plan (``Japanese (Sudachi)``, ``spaCy
    (es_core_news_md)``, ``pymorphy3``).
    """

    label: str
    lemma_of: Callable[[str], Optional[str]]
    content_token_count: Callable[[str], int]


def lemma_provider_for(language) -> Optional[LemmaProvider]:
    """
    The lemma source serving this Language, or None when there is none.

    None means "this language cannot be backfilled", not "no links
    needed": the caller is expected to refuse rather than silently
    produce an empty plan.
    """
    if language is None:
        return None
    for resolve in (_parser_provider, _spacy_provider, _pymorphy_provider):
        provider = resolve(language)
        if provider is not None:
            return provider
    return None


def _word_count(text):
    """
    Number of whitespace-separated words, zero-width spaces included.

    For a space-delimited language this is the whole of
    ``content_token_count``: a "concatenation of several words" (the
    shape the Japanese filter exists to reject, 似ている -> 似るいる)
    cannot occur, so the only thing this number has to decide is whether
    a term is a single word.  It is deliberately *not* the Sudachi
    method's semantic (count of content morphemes) -- do not reuse the
    name for anything that needs that.
    """
    return len([w for w in _WS_RE.split((text or "").replace(ZWS, " ")) if w])


def _installed(module_name):
    """
    True when the module is importable, without importing it.

    Split out so tests can stand in for an engine that is not installed.
    """
    return importlib.util.find_spec(module_name) is not None


def _parser_provider(language):
    "The parser's own get_lemma(), for languages that implement both halves."
    try:
        parser = language.parser
    except ValueError:
        # The language's parser_type is not registered or not installed.
        return None
    if type(parser).get_lemma is AbstractParser.get_lemma:
        return None
    count = getattr(parser, "content_token_count", None)
    if not callable(count):
        return None
    name = getattr(type(parser), "name", None)
    label = name() if callable(name) else (language.parser_type or "parser")
    return LemmaProvider(
        label=label,
        lemma_of=parser.get_lemma,
        content_token_count=count,
    )


def _spacy_provider(language):
    "A spaCy pipeline's lemma_, for the languages the grammar panel serves."
    # Imported here so that importing this module never pulls in the
    # grammar engines (and never imports spaCy itself).
    # pylint: disable-next=import-outside-toplevel
    from lute.read.render.grammar_analysis import spacy_model_for

    model = spacy_model_for(language)
    if model is None:
        return None
    if not _installed("spacy"):
        return None
    if not _installed(model):
        return None
    # pylint: disable-next=import-outside-toplevel
    from lute.read.render.grammar_analysis_matcher import make_spacy_tokenizer

    tokens_for = make_spacy_tokenizer(model)

    def lemma_of(text):
        tokens = tokens_for((text or "").replace(ZWS, " "))
        # One token or nothing: a multi-word term is refused by
        # select_links() anyway (it carries zero-width spaces), and
        # guessing at a joined lemma is exactly the concatenation this
        # tool must not create.
        if len(tokens) != 1:
            return None
        lemma = (tokens[0].get("lemma") or "").strip()
        return lemma or None

    return LemmaProvider(
        label="spaCy (%s)" % model,
        lemma_of=lemma_of,
        content_token_count=_word_count,
    )


def _pymorphy_provider(language):
    "pymorphy3's normal_form, for Russian."
    # pylint: disable-next=import-outside-toplevel
    from lute.read.render.grammar_analysis import is_russian_language

    if not is_russian_language(language):
        return None
    if not _installed("pymorphy3"):
        return None

    # pylint: disable-next=import-outside-toplevel
    from lute.read.render.grammar_analysis_ru import morph_analyzer

    def lemma_of(text):
        words = (text or "").replace(ZWS, " ").split()
        if len(words) != 1:
            return None
        parses = morph_analyzer().parse(words[0])
        if not parses:
            return None
        return parses[0].normal_form or None

    return LemmaProvider(
        label="pymorphy3",
        lemma_of=lemma_of,
        content_token_count=_word_count,
    )
