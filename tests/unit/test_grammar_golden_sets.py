"""Guards for the Korean, Thai and Mandarin grammar golden sets.

Japanese has its own file (``test_grammar_ja_golden.py``) because its set
is derived from a different shape of data; these three share the same
invariants, so they are parametrised over one table:

* every sentence in the set is recognised by the rule it is filed under,
  run through the same matcher the panel uses;
* every hand-written rule has at least one sentence, and no golden key
  names a rule that does not exist;
* a sentence is filed under one rule only;
* the file on disk is what its generator produces -- these sets are
  generated (ko/zh authored in the generator, th derived from the corpus),
  so an edit a regeneration would silently undo has to fail here.

Korean and Mandarin carry a Korean/English/Chinese gloss on every sentence;
Thai's corpus has English and Chinese only, so its Korean gloss is expected
to be absent on the derived rows.
"""

import importlib
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
_DATA = os.path.join(_ROOT, "lute", "jlpt_data")

# engine module, golden file, rule attribute, script module, own-text field,
# glosses every sentence must carry
_LANGS = [
    (
        "ko",
        "grammar_analysis_ko",
        "grammar_ko_golden.json",
        "_KO_RULES",
        "generate_grammar_ko_golden",
        "en",
    ),
    (
        "th",
        "grammar_analysis_th",
        "grammar_th_golden.json",
        "_TH_RULES",
        "generate_grammar_th_golden",
        "zh",
    ),
    (
        "zh",
        "grammar_analysis_zh",
        "grammar_zh_golden.json",
        "_ZH_RULES",
        "generate_grammar_zh_golden",
        "en",
    ),
]


# The tokenizer each golden set tokenises its sentences with.  CI installs
# develop deps only (see ci.yml: the engines are covered by
# nightly-guardrail's grammar-engines job), so a missing one skips that set
# instead of failing on ModuleNotFoundError.  Mandarin needs none.
_TOKENIZER = {"ko": "kiwipiepy", "th": "pythainlp", "zh": None}


def _engine(tag):
    package = _TOKENIZER.get(tag)
    if package:
        pytest.importorskip(package)
    return importlib.import_module(f"lute.read.render.{tag}")


def _rules(engine, attribute):
    return {rule["key"]: rule for rule in getattr(engine, attribute)}


def _golden(engine):
    return engine._golden_examples()  # pylint: disable=protected-access


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_golden_file_exists(code, module, filename, attr, script, gloss):
    assert os.path.exists(os.path.join(_DATA, filename)), f"no {filename}"


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_golden_keys_name_real_rules(code, module, filename, attr, script, gloss):
    engine = _engine(module)
    rules = _rules(engine, attr)
    unknown = sorted(set(_golden(engine)) - set(rules))
    assert not unknown, f"{code}: golden entries with no rule: {unknown}"


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_every_rule_has_a_golden_example(code, module, filename, attr, script, gloss):
    "A rule with no golden sentence is a rule no test ever exercises."
    engine = _engine(module)
    rules = _rules(engine, attr)
    missing = sorted(set(rules) - set(_golden(engine)))
    assert not missing, f"{code}: hand-written rules with no golden example: {missing}"


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_golden_sentences_fire_on_their_own_rule(
    code, module, filename, attr, script, gloss
):
    """
    Every curated sentence must be recognised by the rule it is filed under.

    This is the invariant that keeps the /grammar page honest: a point
    whose own example does not match its own matcher is a point the reader
    can click through to and find broken.
    """
    engine = _engine(module)
    rules = _rules(engine, attr)
    matcher = importlib.import_module("lute.read.render.grammar_analysis_matcher")
    for key, examples in _golden(engine).items():
        rule = rules[key]
        for example in examples:
            text = example["text"]
            if hasattr(engine, "_match_spans"):
                fired = engine._match_spans(  # pylint: disable=protected-access
                    rule, engine._tokens_for(text), text
                )
            else:
                fired = matcher.match_rule(rule, engine._tokens_for(text), text)
            assert fired, f"{code}: golden sentence does not fire on {key}: {text}"


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_golden_sentences_are_not_filed_under_two_rules(
    code, module, filename, attr, script, gloss
):
    "One sentence illustrates one point."
    seen = {}
    for key, examples in _golden(_engine(module)).items():
        for example in examples:
            text = example["text"]
            assert (
                text not in seen
            ), f"{code}: {text} filed under {seen[text]} and {key}"
            seen[text] = key


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_golden_sentences_carry_the_required_gloss(
    code, module, filename, attr, script, gloss
):
    "Every sentence carries the gloss its language is expected to have."
    missing = [
        example["text"]
        for examples in _golden(_engine(module)).values()
        for example in examples
        if not (example.get(gloss) or "").strip()
    ]
    assert not missing, f"{code}: sentences without a {gloss} gloss: {missing}"


@pytest.mark.parametrize("code,module,filename,attr,script,gloss", _LANGS)
def test_golden_file_matches_the_generator_output(
    code, module, filename, attr, script, gloss
):
    """
    The file is generated, so it has to stay what the generator produces.

    Guards against an edit that a regeneration would silently undo.
    """
    generator = importlib.import_module(f"scripts.{script}")
    with open(os.path.join(_DATA, filename), encoding="utf-8") as fh:
        on_disk = json.load(fh)
    generated, _missing, _failures = generator.collect()
    assert on_disk == generated, (
        f"{filename} differs from the generator output; "
        f"re-run python -m scripts.{script} --write"
    )
