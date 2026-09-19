"""Coverage of the 한국어 display table (_KO_BY_KEY) across matcher engines."""

from lute.read.render import grammar_analysis_matcher as matcher

# Every engine that renders through the shared matcher's _desc (the
# Japanese engine has its own driver and is intentionally not listed:
# its data-driven rules keep their English meanings for now).
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
