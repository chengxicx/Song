"""
Pure mapping rules between Lute terms and the Eudic studylist.

Everything here is a side-effect-free function so the sync rules can be
tested without a db, a network, or a Flask app.

Three things about Eudic drive the shape of this module, all confirmed
against the live API rather than the docs:

1. ``language`` is a *product bucket* (en/fr/de/es -> 欧路词典 / 法语助手 /
   德语助手 / 西语助手), not the language of the words inside it.  A
   Japanese word list lives happily in the ``en`` bucket as its own
   category, so any Lute language can be synced as long as the user gives
   us the bucket and the category id.

2. A word's reading is part of the **storage key**.  ``GET /word?word=俺``
   is a 404 while ``word=俺 [おれ]`` is a hit, so the remote key is the
   whole string including ``" [おれ]"``.  Pushing a bare ``俺`` next to an
   existing ``俺 [おれ]`` therefore creates a second entry, and every
   subsequent sync adds another one.  Hence the split below between
   ``word_key`` (what we match on) and ``push_form`` (what we send).

3. Eudic's word list API can only carry ``word`` and ``star`` in bulk
   (``POST /studylist/words`` takes ``items: [{word, star}]``), so a
   term's translation and example sentence never leave Lute.  That is a
   capability limit of the API, not a bug here.
"""

import re

# Zero-width space: Lute's multi-word terms are stored as "foo\u200Bbar".
ZWS = "\u200B"

# The four buckets the OpenAPI accepts; anything else returns 400.
EUDIC_BUCKETS = ("en", "fr", "de", "es")

# Lute statuses worth sending.  0 (Unknown) and 98 (Ignored) are local
# decisions that must not delete anything on the Eudic side.
PUSHABLE_STATUSES = (1, 2, 3, 4, 5, 99)

# Statuses a pull must never overwrite.
LOCKED_STATUSES = (98, 99)

_STAR_MIN = 1
_STAR_MAX = 5

# Trailing "[reading]" or "【reading】", with optional space before it.
_READING_RE = re.compile(r"\s*[\[【][^\]】]*[\]】]\s*$")

_WS_RE = re.compile(r"\s+")

_NBSP = "\u00A0"

# Scripts written without spaces between words: for these a zero-width
# space is a token boundary *inside* one word and must be dropped, not
# turned into a space ("気\u200Bが\u200Bつく" is "気がつく", never
# "気 が つく").  For everything else it marks a real word boundary.
_CJK_RE = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]"
)


def normalize_word(text):
    """
    Clean a raw word for transport.

    Zero-width spaces become a space in space-delimited languages and
    vanish in CJK ones; non-breaking and ideographic spaces become plain
    spaces; runs of whitespace collapse.
    """
    s = (text or "").replace(_NBSP, " ").replace("\u3000", " ")
    if ZWS in s:
        s = s.replace(ZWS, "" if _CJK_RE.search(s) else " ")
    return _WS_RE.sub(" ", s).strip()


def split_reading(text):
    """
    Split "俺 [おれ]" into ("俺", "おれ").

    A trailing bracketed segment is Eudic's reading notation; anything
    without one comes back with an empty reading.
    """
    s = normalize_word(text)
    match = _READING_RE.search(s)
    if match is None:
        return s, ""
    word = s[: match.start()].strip()
    reading = s[match.start() :].strip()[1:-1].strip()
    if not word:
        # The whole thing was bracketed: keep it as the word, no reading.
        return s, ""
    return word, reading


def strip_reading(text):
    "The word without its trailing [reading] marker."
    return split_reading(text)[0]


def word_key(text):
    """
    The key used to match a Lute term against a remote word.

    Case- and reading-insensitive, because ``俺 [おれ]`` and ``俺`` are the
    same vocabulary item even though Eudic stores them as different
    strings.  Whitespace is not significant inside CJK words, so a remote
    ``気 が つく`` still matches Lute's ``気がつく``.
    """
    w = strip_reading(text)
    if _CJK_RE.search(w):
        w = _WS_RE.sub("", w)
    return w.lower()


def push_form(word, reading="", remote_word=None):
    """
    The exact string to send to Eudic for a term.

    If the remote already has a matching key, reuse its original string
    verbatim so we only change the star and never create a duplicate.
    Otherwise rebuild Eudic's notation from the Lute term.
    """
    if remote_word:
        return remote_word
    w = normalize_word(word)
    r = normalize_word(reading)
    if r:
        return f"{w} [{r}]"
    return w


def star_for_status(status):
    """
    The Eudic star for a Lute status, or None if it must not be pushed.

    Eudic has no writable "mastered" list (``/mastered_words`` is GET and
    DELETE only), so Well Known degrades to the top star.
    """
    try:
        s = int(status)
    except (TypeError, ValueError):
        return None
    if s == 99:
        return _STAR_MAX
    if s in (1, 2, 3, 4, 5):
        return s
    return None


def status_for_star(star):
    "The Lute status for an Eudic star, or None if the star is unusable."
    try:
        s = int(star)
    except (TypeError, ValueError):
        return None
    if _STAR_MIN <= s <= _STAR_MAX:
        return s
    return None


def is_pushable(status):
    "True if terms at this status should be sent to Eudic."
    return star_for_status(status) is not None


def clean_phon(phon):
    """
    Normalise a phonetic string: Eudic returns "/'ækʃn/" with slashes,
    Lute stores the bare text.
    """
    s = normalize_word(phon)
    return s.strip("/[]").strip()


def clean_exp(exp):
    "Normalise a translation string, dropping stray line breaks."
    return _WS_RE.sub(" ", (exp or "").replace("\r", " ").replace("\n", " ")).strip()


def truncate(value, length):
    "Clip a value to a column width; None stays None."
    if value is None:
        return None
    return (value or "")[:length]
