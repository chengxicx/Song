"""Tests for the French grammar-analysis engine (spaCy fr_core_news_sm)."""

import pytest

pytest.importorskip("spacy")
pytest.importorskip("fr_core_news_sm")

from lute.read.render.grammar_analysis_fr import (
    _FR_RULES,
    _get_all_rules,
    analyze_french,
)
from lute.read.render.grammar_analysis import is_french_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_french(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "fr_il_y_a": "Il y a un café près de chez moi.",
    "fr_estceque": "Est-ce que tu viens ce soir ?",
    "fr_c_est": "C'est très bon.",
    "fr_wh_questions": "Comment allez-vous ?",
    "fr_negation": "Je ne sais pas.",
    "fr_possessives": "Mon frère habite ici.",
    "fr_demo": "Ce livre est bon.",
    "fr_futur_proche": "Je vais partir demain.",
    "fr_passe_compose": "Hier j'ai mangé une pomme.",
    "fr_imparfait": "Quand il était petit, il jouait dehors.",
    "fr_futur_simple": "Elle sera là demain.",
    "fr_modals": "Je peux venir ce soir.",
    "fr_comparative": "Il est plus grand que moi.",
    "fr_partitif": "Je mange du pain.",
    "fr_imperatif": "Parlez !",
    "fr_pronominal": "Il se lève à sept heures.",
    "fr_inversion": "Voyage-t-il souvent en Europe?",
    "fr_venir_de": "Je viens de manger.",
    "fr_depuis": "J'habite ici depuis deux ans.",
    "fr_relative": "La femme qui parle est ma mère.",
    "fr_subjonctif": "Je doute qu'il soit là.",
    "fr_il_faut": "Il faut partir tôt.",
    "fr_conditionnel": "Je voudrais un café.",
    "fr_ce_que": "Je sais ce que tu veux.",
    "fr_y_en": "J'en veux encore.",
    "fr_sans_inf": "Il est parti sans dire au revoir.",
    "fr_superlatif": "C'est le plus grand spectacle du monde.",
    "fr_avant_de": "Il devrait réfléchir avant de parler.",
    "fr_si_conditionnel": "Si j'avais de l'argent, je voyagerais.",
    "fr_gerondif": "Il apprend en travaillant.",
    "fr_subjonctif_passe": "Nous sommes ravis qu'elle ait gagné la médaille d'or.",
    "fr_plus_que_parfait": "Il n'avait pas pu les joindre.",
    "fr_apres_avoir": "Après avoir mangé, il est sorti.",
    "fr_futur_anterieur": "Elle vous téléphonera dès qu'elle aura fini son roman.",
    "fr_passe_simple": "Il fit tout son possible.",
    "fr_pendant": "Il a habité pendant cinq ans à Londres.",
    "fr_faire_causatif": "Il fait réparer la télévision.",
    "fr_des_que": "Je t'écrirai dès que je serai à Rio.",
    "fr_pour_que": "Je t'explique pour que tu comprennes.",
    "fr_bien_que": "Bien qu'il soit fatigué, il lit le journal.",
    "fr_comme_si": "Il agit comme si tout était facile.",
    "fr_a_moins_que": "À moins qu'il ne pleuve, nous sortirons.",
    "fr_ne_que": "Il n'y a que cinq étudiants dans la classe.",
}

# The homographic complementizer "que": ne ... que must stay off these.
_NOT_RESTRICTIVE = [
    "Je ne crois pas que Daniel soit coupable.",
    "Je ne pense pas qu'il vienne.",
    "Il ne faut pas que vous partiez.",
    "Elle ne veut pas que nous l'attendions.",
    "Il ne sais pas ce que tu veux.",
    "Il ne démissionnera pas pourvu que le directeur lui donne une augmentation.",
]


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the construction, not the whole sentence."
    for e in analyze_french("Il y a un café près de chez moi."):
        if e["key"] == "fr_il_y_a":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "Il y a"


def test_compound_tenses_are_distinct():
    "Passé composé, plus-que-parfait and futur antérieur keep their own rows."
    text = "J'ai mangé. Il avait déjà mangé. Quand il aura fini, nous partirons."
    keys = _keys(text)
    assert "fr_passe_compose" in keys
    assert "fr_plus_que_parfait" in keys
    assert "fr_futur_anterieur" in keys


def test_subjunctive_past_is_not_a_passe_compose():
    "qu'il ait fini is a past subjunctive, not a spoken past."
    keys = _keys("Je doute qu'il ait fini.")
    assert "fr_subjonctif_passe" in keys
    assert "fr_passe_compose" not in keys


def test_ne_que_ignores_the_homographic_complementizer():
    "Only the restrictive ne ... que is a point; 'que' + clause is not."
    for sentence in _NOT_RESTRICTIVE:
        assert "fr_ne_que" not in _keys(sentence), sentence
    assert "fr_ne_que" in _keys("Il n'y a que cinq étudiants dans la classe.")


def test_curly_apostrophe_is_normalised():
    "Books mix ' and ’ freely; every elision spec must fire on both."
    straight = _keys("C'est une bonne idée.")
    curly = _keys("C’est une bonne idée.")
    assert "fr_c_est" in straight
    assert straight == curly


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_french("Il y a un café.", "zh")}
    en = {e["key"]: e for e in analyze_french("Il y a un café.", "en")}
    assert "存在" in zh["fr_il_y_a"]["desc"]
    assert "existence" in en["fr_il_y_a"]["desc"]


def test_levels_are_cefr():
    "The French engine grades points with CEFR levels."
    levels = {
        e["key"]: e["level"] for e in analyze_french(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["fr_il_y_a"] == "A1"
    assert levels["fr_passe_compose"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_french("Il y a un café. Je ne sais pas."):
        assert entry["name"]
        assert entry["desc"]
        assert entry["examples"]
        ex = entry["examples"][0]
        assert ex["sentence"]
        assert len(ex["matches"]) >= 1
        for m in ex["matches"]:
            assert m["start"] < m["end"]


class StubLanguage:
    def __init__(self, parser_type=None, name=None):
        self.parser_type = parser_type
        self.name = name


def test_is_french_language_detects_by_name():
    "French detection works on language names (parser_type is generic spacedel)."
    assert is_french_language(StubLanguage(name="French"))
    assert is_french_language(StubLanguage(name="Français"))
    assert is_french_language(StubLanguage(name="法语"))
    assert not is_french_language(StubLanguage(name="German"))
    assert not is_french_language(StubLanguage(parser_type="spacedel"))
    assert not is_french_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_FR_RULES) >= 20
    assert len(_get_all_rules()) >= len(_FR_RULES)


# ---- golden examples ----------------------------------------------------
#
# lute/jlpt_data/grammar_fr_golden.json holds corpus sentences that each
# hand-written rule must keep matching.  The sentences came out of a local
# grammar-book extraction, so they are real prose rather than invented
# examples, and a change that narrows a rule enough to lose them fails here.
# The file lives beside the grammar library because the /grammar page serves
# the same sentences as each point's examples.

import json  # noqa: E402
import os  # noqa: E402

_GOLDEN_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "lute",
        "jlpt_data",
        "grammar_fr_golden.json",
    )
)


def _golden():
    if not os.path.exists(_GOLDEN_PATH):
        pytest.skip("no fr golden set yet")
    with open(_GOLDEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_golden_examples_fire_their_rule():
    "Every golden sentence must still be recognised by its own rule."
    golden = _golden()
    assert golden, "golden set is empty"
    for key, entry in golden.items():
        for ex in entry["examples"]:
            hits = _keys(ex["fr"])
            assert key in hits, f"{key} lost its golden sentence: {ex['fr']!r}"


def test_golden_names_match_the_rules():
    "A golden entry's name must be the display name of its rule."
    names = {r["key"]: r["name"] for r in _FR_RULES}
    for key, entry in _golden().items():
        assert key in names, f"golden key {key} is not a rule"
        assert entry["name"] == names[key], f"{key}: stale name {entry['name']!r}"
