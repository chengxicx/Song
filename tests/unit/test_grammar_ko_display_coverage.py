"""Coverage of the 한국어 display table (_KO_BY_KEY) across matcher engines."""

import json
import os

from lute.read.render import grammar_analysis_matcher as matcher
from lute.read.render.grammar_analysis_ja import _ALL_RULES, _DATA_RULES

# Every engine that renders through the shared matcher's _desc (the
# Japanese engine has its own driver and is covered separately below).
_MATCHER_ENGINES = ["en", "es", "ru", "fr", "de", "it", "pt", "th", "ar", "zh", "yue"]


def _rule_keys(tag):
    mod = __import__(
        f"lute.read.render.grammar_analysis_{tag}",
        fromlist=["_RULES"],
    )
    rules = [v for k, v in vars(mod).items() if k.startswith("_") and k.endswith("_RULES")]
    assert rules, f"no _*_RULES list found in grammar_analysis_{tag}"
    return {r["key"] for r in rules[0]}


def test_every_matcher_rule_has_a_korean_description():
    "A rule without a _KO_BY_KEY entry would silently show English text."
    for tag in _MATCHER_ENGINES:
        keys = _rule_keys(tag)
        missing = sorted(keys - set(matcher._KO_BY_KEY))
        assert not missing, f"grammar_analysis_{tag} rules lack ko descriptions: {missing}"


def test_ko_table_has_no_stale_keys():
    "Keys in _KO_BY_KEY must name real rules (guards against typos)."
    all_keys = set()
    for tag in _MATCHER_ENGINES:
        all_keys |= _rule_keys(tag)
    stale = sorted(set(matcher._KO_BY_KEY) - all_keys)
    assert not stale, f"_KO_BY_KEY entries name no rule: {stale}"


# ---- Japanese engine (own driver; glosses live in grammar/ko.json) ----

_JA_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "lute", "jlpt_data", "grammar",
)


def _ja_data_ids():
    "All curated entry ids across the five level files."
    ids = set()
    for level in ["n5", "n4", "n3", "n2", "n1"]:
        path = os.path.join(_JA_DATA_DIR, f"{level}.json")
        with open(path, encoding="utf-8") as fh:
            ids.update(e["id"] for e in json.load(fh))
    return ids


def test_japanese_ko_json_covers_every_data_id():
    "ko.json must carry a Korean gloss for every curated entry id."
    ko = json.load(open(os.path.join(_JA_DATA_DIR, "ko.json"), encoding="utf-8"))
    missing = sorted(_ja_data_ids() - set(ko))
    assert not missing, f"curated ids lack a ko.json gloss: {missing}"


def test_japanese_ko_json_has_no_stale_ids():
    "ko.json keys must name real curated entries (guards against typos)."
    ko = json.load(open(os.path.join(_JA_DATA_DIR, "ko.json"), encoding="utf-8"))
    stale = sorted(set(ko) - _ja_data_ids())
    assert not stale, f"ko.json keys name no curated entry: {stale}"


def test_japanese_every_rule_has_a_korean_description():
    "Data rules get meaning_ko from ko.json; hand-written rules from _KO_HAND."
    missing = sorted(r["key"] for r in _ALL_RULES if not (r.get("meaning_ko") or "").strip())
    assert not missing, f"Japanese rules lack ko descriptions: {missing}"
    assert len(_DATA_RULES) >= 500
