"""
Tests for the browsable grammar library (lute/grammar).

The reading panel only shows the points that happen to be *in* the page
being read, so a corpus entry the reader has not run into yet is
invisible.  The library lists the whole corpus per language.  The
`library_entries` builders are tested directly (structure, level order,
reference-only handling, cross-links) and the routes through the test
client, including the explanation page a corpus-less language gets
instead of a 404.

The engines import their tokenizers lazily and skip the dataset on
ImportError, so the data-rich assertions need the parser installed;
`_need_sudachi` / `_need_kiwipiepy` gate those.
"""

import importlib

import markupsafe
import pytest

from lute.db import db
from lute.grammar.routes import _level_class, _level_counts
from lute.read.render import grammar_analysis_ja as ja
from tests.utils import make_book

_JA_RANK = {"N5": 0, "N4": 1, "N3": 2, "N2": 3, "N1": 4}
_KO_RANK = {"TOPIK 1-2": 0, "TOPIK 3-4": 1, "TOPIK 5-6": 2}
_CEFR_RANK = {"A1": 0, "A2": 1, "B1": 2, "B2": 3, "C1": 4, "C2": 5}


def _need_sudachi():
    "The Japanese corpus is only loaded when Sudachi is installed."
    pytest.importorskip("sudachipy")


def _ko_entries(display_lang="en"):
    "library_entries() for Korean, skipping when kiwipiepy is absent."
    ko = pytest.importorskip("lute.read.render.grammar_analysis_ko")
    return ko.library_entries(display_lang)


def _ru_entries(display_lang="en"):
    """
    library_entries() for Russian.

    The merged data rules validate themselves against pymorphy3
    tokenisations of their own examples, so the corpus needs it.
    """
    pytest.importorskip("pymorphy3")
    ru = pytest.importorskip("lute.read.render.grammar_analysis_ru")
    return ru.library_entries(display_lang)


def _es_entries(display_lang="en"):
    """
    library_entries() for Spanish.

    The Spanish corpus is the hand-written rules and their golden
    examples -- no tokeniser is touched, so it renders even without
    spaCy installed.
    """
    es = pytest.importorskip("lute.read.render.grammar_analysis_es")
    return es.library_entries(display_lang)


def _fr_entries(display_lang="en"):
    """
    library_entries() for French.

    The data rules validate their specs against spaCy tokenisations of
    their own examples, so the corpus needs the French model.
    """
    pytest.importorskip("spacy")
    pytest.importorskip("fr_core_news_sm")
    fr = pytest.importorskip("lute.read.render.grammar_analysis_fr")
    return fr.library_entries(display_lang)


def _de_entries(display_lang="en"):
    """
    library_entries() for German.

    Same shape as the French corpus: hand-written rules plus merged
    material rows, both validated against spaCy tokenisations of their
    own examples, so the German model has to be installed.
    """
    pytest.importorskip("spacy")
    pytest.importorskip("de_core_news_sm")
    de = pytest.importorskip("lute.read.render.grammar_analysis_de")
    return de.library_entries(display_lang)


# ---- entry builders -------------------------------------------------


def test_ja_entries_are_level_ordered_and_covered_by_the_rank_map():
    "N5 first, N1 last (stable within a level), and every key unique."
    entries = ja.library_entries("en")
    assert entries
    assert all(e["level"] in _JA_RANK for e in entries)
    ranks = [_JA_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    keys = [e["key"] for e in entries]
    assert len(set(keys)) == len(keys)


def test_ja_entries_carry_the_core_fields():
    for e in ja.library_entries("en"):
        assert e["key"] and e["name"] and e["level"]
        assert isinstance(e["examples"], list)
        assert isinstance(e["skipped"], bool)


def test_ko_entries_are_level_ordered_and_unique():
    entries = _ko_entries("en")
    assert entries
    assert all(e["level"] in _KO_RANK for e in entries)
    ranks = [_KO_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    assert len({e["key"] for e in entries}) == len(entries)


def test_ru_entries_are_cefr_ordered_and_unique():
    "A1 first, C2 last (stable within a level), and every key unique."
    entries = _ru_entries("en")
    assert entries
    assert all(e["level"] in _CEFR_RANK for e in entries)
    ranks = [_CEFR_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    assert len({e["key"] for e in entries}) == len(entries)


def test_es_entries_are_cefr_ordered_and_unique():
    "Same CEFR order discipline as the Russian corpus."
    entries = _es_entries("en")
    assert entries
    assert all(e["level"] in _CEFR_RANK for e in entries)
    ranks = [_CEFR_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    assert len({e["key"] for e in entries}) == len(entries)


def test_fr_entries_are_cefr_ordered_and_unique():
    "Same CEFR order discipline as the Spanish corpus."
    entries = _fr_entries("en")
    assert entries
    assert all(e["level"] in _CEFR_RANK for e in entries)
    ranks = [_CEFR_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    assert len({e["key"] for e in entries}) == len(entries)


def test_de_entries_are_cefr_ordered_and_unique():
    "Same CEFR order discipline as the French corpus."
    entries = _de_entries("en")
    assert entries
    assert all(e["level"] in _CEFR_RANK for e in entries)
    ranks = [_CEFR_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    assert len({e["key"] for e in entries}) == len(entries)


def test_de_entries_carry_the_core_fields():
    """
    Every German point renders with a name, a description and examples.

    A row with no example would render an empty card: the detail page has
    nothing else to show, so the reader cannot tell the point from a stub.
    """
    for e in _de_entries("en"):
        assert e["key"] and e["name"] and e["level"] and e["desc"]
        assert e["examples"], f"no examples for {e['key']}"
        for row in e["examples"]:
            assert row["text"].strip(), f"empty example text in {e['key']}"


def test_de_entries_span_the_whole_cefr_scale():
    """
    German grades from A1 to C2, and every band must actually be populated.

    A corpus that quietly stops at B2 looks complete but teaches half the
    grammar the engine can recognise.
    """
    levels = {e["level"] for e in _de_entries("en")}
    assert levels == set(_CEFR_RANK), f"missing CEFR bands: {set(_CEFR_RANK) - levels}"


def test_de_entries_switch_description_language():
    "Chinese display prefers the zh gloss over the English one."
    zh = {e["key"]: e for e in _de_entries("zh")}
    en = {e["key"]: e for e in _de_entries("en")}
    switched = [k for k in zh if zh[k]["desc"] != en[k]["desc"]]
    assert switched, "zh display produced no different descriptions"


# ---- the rule-set-only engines (en / zh / yue / ar / it / pt) ----------
#
# Their corpus is the hand-written rule set itself: every rule the panel
# can fire is a browsable row, and the rows carry no examples (the rule
# sets were written without any).  Importing a module is dependency-free
# -- the spaCy / pyarabic tokenizers load lazily -- so these tests need
# no parser installed.

_RULESET_MODULES = (
    "grammar_analysis_en",
    "grammar_analysis_zh",
    "grammar_analysis_yue",
    "grammar_analysis_ar",
    "grammar_analysis_it",
    "grammar_analysis_pt",
)

_RULESET_RULE_ATTRS = {
    "grammar_analysis_en": "_EN_RULES",
    "grammar_analysis_zh": "_ZH_RULES",
    "grammar_analysis_yue": "_YUE_RULES",
    "grammar_analysis_ar": "_AR_RULES",
    "grammar_analysis_it": "_IT_RULES",
    "grammar_analysis_pt": "_PT_RULES",
}


def _ruleset_engine(module_name):
    module = importlib.import_module("lute.read.render." + module_name)
    return module, getattr(module, _RULESET_RULE_ATTRS[module_name])


@pytest.mark.parametrize("module_name", _RULESET_MODULES)
def test_ruleset_entries_are_cefr_ordered_and_unique(module_name):
    "Same CEFR order discipline as the Spanish corpus."
    module, _ = _ruleset_engine(module_name)
    entries = module.library_entries("en")
    assert entries
    assert all(e["level"] in _CEFR_RANK for e in entries)
    ranks = [_CEFR_RANK[e["level"]] for e in entries]
    assert ranks == sorted(ranks)
    assert len({e["key"] for e in entries}) == len(entries)


@pytest.mark.parametrize("module_name", _RULESET_MODULES)
def test_ruleset_entries_list_exactly_the_panel_rules(module_name):
    """
    The browsable list is the rule set itself: no row the panel can never
    fire, and no panel rule missing from the page.

    Both directions matter -- a phantom row is a dead link, and a missing
    one hides a point the reader just met on the page from the library.
    """
    module, rules = _ruleset_engine(module_name)
    entries = module.library_entries("en")
    assert {e["key"] for e in entries} == {r["key"] for r in rules}


@pytest.mark.parametrize("module_name", _RULESET_MODULES)
def test_ruleset_entries_carry_the_core_fields(module_name):
    """
    Every rule renders with a name and a description, and no examples.

    These rule sets carry no example sentences, so the rows come back
    with an empty list and the detail template hides the example block:
    an empty examples section would read like a stub.  Nothing is
    reference-only -- every rule is something the matcher can fire.
    """
    module, _ = _ruleset_engine(module_name)
    for e in module.library_entries("en"):
        assert e["key"] and e["name"] and e["level"] and e["desc"]
        assert e["examples"] == []
        assert e["skipped"] is False


@pytest.mark.parametrize("module_name", _RULESET_MODULES)
def test_ruleset_entries_switch_description_language(module_name):
    "Chinese display prefers the zh wording over the English one."
    module, _ = _ruleset_engine(module_name)
    zh = {e["key"]: e for e in module.library_entries("zh")}
    en = {e["key"]: e for e in module.library_entries("en")}
    switched = [k for k in zh if zh[k]["desc"] != en[k]["desc"]]
    assert switched, "zh display produced no different descriptions"


def test_ru_es_fr_entries_carry_the_core_fields():
    "All three corpora speak the same entry shape the templates render."
    for entries in (_ru_entries("en"), _es_entries("en"), _fr_entries("en")):
        for e in entries:
            assert e["key"] and e["name"] and e["level"]
            assert isinstance(e["examples"], list)
            assert e["skipped"] is False


def test_fr_examples_carry_every_display_language():
    """
    Each golden sentence ships a translation in every display language the
    app offers, so the detail page never falls back to English under a
    Chinese or Korean heading.
    """
    fr = pytest.importorskip("lute.read.render.grammar_analysis_fr")
    golden = fr._golden_examples()  # pylint: disable=protected-access
    assert golden, "no golden set"
    bare = [
        f"{key}: {ex.get('fr')} [{field}]"
        for key, rows in golden.items()
        for ex in rows
        for field in ("fr", "zh", "en", "ko")
        if not (ex.get(field) or "").strip()
    ]
    assert bare == [], f"examples missing a translation: {bare}"


def test_fr_library_examples_switch_the_gloss():
    """
    The sentence stays French in every display language; only the line
    under it changes.
    """
    by_lang = {lang: _fr_entries(lang) for lang in ("en", "zh", "ko")}
    key = next(e["key"] for e in by_lang["en"] if e["examples"])
    rows = {
        lang: next(e for e in entries if e["key"] == key)["examples"]
        for lang, entries in by_lang.items()
    }
    texts = {lang: [r["text"] for r in rows[lang]] for lang in rows}
    assert texts["en"] == texts["zh"] == texts["ko"], "the sentence must not change"
    for lang in ("en", "zh", "ko"):
        assert all(r["tr"] for r in rows[lang]), f"{key}: empty {lang} gloss"


def test_es_examples_carry_every_display_language():
    """
    Each golden sentence ships a translation in every display language the
    app offers, so the detail page never falls back to English under a
    Chinese or Korean heading.
    """
    es = pytest.importorskip("lute.read.render.grammar_analysis_es")
    golden = es._golden_examples()  # pylint: disable=protected-access
    assert golden, "no golden set"
    bare = [
        f"{key}: {ex.get('es')} [{field}]"
        for key, rows in golden.items()
        for ex in rows
        for field in ("es", "zh", "en", "ko")
        if not (ex.get(field) or "").strip()
    ]
    assert bare == [], f"examples missing a translation: {bare}"


def test_es_library_examples_switch_the_gloss():
    """
    The sentence stays Spanish in every display language; only the line
    under it changes.
    """
    by_lang = {lang: _es_entries(lang) for lang in ("en", "zh", "ko")}
    key = next(e["key"] for e in by_lang["en"] if e["examples"])
    rows = {
        lang: next(e for e in entries if e["key"] == key)["examples"]
        for lang, entries in by_lang.items()
    }
    texts = {lang: [r["text"] for r in rows[lang]] for lang in rows}
    assert texts["en"] == texts["zh"] == texts["ko"], "the sentence must not change"
    for lang in ("en", "zh", "ko"):
        assert all(r["tr"] for r in rows[lang]), f"{key}: empty {lang} gloss"
    first = {rows[lang][0]["tr"] for lang in rows}
    assert len(first) == 3, f"{key}: the three glosses should differ, got {first}"


def test_th_library_examples_split_the_corpus_gloss():
    """
    Thai stores "thai · roman — english ／ 中文" in one string; the library
    hands the template {"text", "tr"} so the Chinese becomes its own line
    and stays hidden when Chinese is not the display language.
    """
    th = pytest.importorskip("lute.read.render.grammar_analysis_th")
    for lang, want_gloss in (("en", False), ("zh", True)):
        rows = [r for e in th.library_entries(lang) for r in e["examples"]]
        assert rows, "no Thai examples"
        assert all(" ／ " not in r["text"] for r in rows), "gloss left in the text"
        assert all(bool(r["tr"]) is want_gloss for r in rows), f"{lang}: wrong gloss"


def test_ru_examples_carry_every_display_language():
    """
    Same contract as the Spanish set: each golden sentence ships a
    translation in every display language the app offers.
    """
    ru = pytest.importorskip("lute.read.render.grammar_analysis_ru")
    golden = ru._golden_examples()  # pylint: disable=protected-access
    assert golden, "no golden set"
    bare = [
        f"{key}: {ex.get('ru')} [{field}]"
        for key, rows in golden.items()
        for ex in rows
        for field in ("ru", "zh", "en", "ko")
        if not (ex.get(field) or "").strip()
    ]
    assert bare == [], f"examples missing a translation: {bare}"


def test_ru_library_examples_switch_the_gloss():
    """
    The sentence stays Russian in every display language; only the line
    under it changes, and the stress marks stay stripped.
    """
    by_lang = {lang: _ru_entries(lang) for lang in ("en", "zh", "ko")}
    key = next(e["key"] for e in by_lang["en"] if e["examples"])
    rows = {
        lang: next(e for e in entries if e["key"] == key)["examples"]
        for lang, entries in by_lang.items()
    }
    texts = {lang: [r["text"] for r in rows[lang]] for lang in rows}
    assert texts["en"] == texts["zh"] == texts["ko"], "the sentence must not change"
    for lang in ("en", "zh", "ko"):
        assert all(r["tr"] for r in rows[lang]), f"{key}: empty {lang} gloss"
    first = {rows[lang][0]["tr"] for lang in rows}
    assert len(first) == 3, f"{key}: the three glosses should differ, got {first}"


def test_ru_data_rows_quote_their_chinese_gloss():
    """
    The merged rows keep a parallel Chinese translation in `example_zh`;
    the card prints it under the sentence instead of dropping it.
    """
    entries = _ru_entries("zh")
    data = [e for e in entries if e["key"].startswith("rgm_")]
    assert data, "no data rows in the library"
    assert any(r["tr"] for e in data for r in e["examples"]), "gloss never used"
    english = {e["key"]: e for e in _ru_entries("en")}
    for entry in data:
        assert all(
            not r["tr"] for r in english[entry["key"]]["examples"]
        ), f"{entry['key']}: the Chinese gloss leaked into the English page"


def test_library_descriptions_switch_to_korean():
    """
    The /grammar page resolves a rule's description through the same table
    the panel uses, so a Korean reader never gets Korean example lines
    under an English heading.
    """
    matcher = pytest.importorskip("lute.read.render.grammar_analysis_matcher")
    for name, entries_for in (("es", _es_entries), ("ru", _ru_entries)):
        english = {e["key"]: e["desc"] for e in entries_for("en")}
        korean = {e["key"]: e["desc"] for e in entries_for("ko")}
        assert set(english) == set(korean), f"{name}: key set changed with the language"
        covered = [
            k for k in english if k in matcher._KO_BY_KEY
        ]  # pylint: disable=protected-access
        assert covered, f"{name}: no rule resolves a Korean description"
        untranslated = [k for k in covered if korean[k] == english[k]]
        assert (
            untranslated == []
        ), f"{name}: English description under ko: {untranslated}"


def test_ru_entries_have_examples_from_their_own_store():
    """
    Hand-written rules quote their golden sentences, data rows their merged
    examples -- a library card never renders without at least the point's
    own prose unless the corpus genuinely has none for it.
    """
    entries = _ru_entries("en")
    with_examples = [e for e in entries if e["examples"]]
    assert with_examples, "no library entry carries examples"
    bare = [e["key"] for e in entries if not e["examples"]]
    assert len(bare) < len(entries) / 4, f"too many example-less rows: {bare}"


def test_ru_data_rows_carry_formation_from_the_library():
    "The merged rows quote the textbook's formation line on their card."
    entries = _ru_entries("en")
    assert any(e.get("formation") for e in entries)


def test_ru_es_zh_display_uses_the_rule_gloss():
    "The zh panel language swaps the description where a gloss exists."
    for get in (_ru_entries, _es_entries):
        en = get("en")
        zh = get("zh")
        by_key = {e["key"]: e for e in zh}
        swapped = [e["key"] for e in en if by_key[e["key"]]["desc"] != e["desc"]]
        assert swapped, f"{get.__name__}: no description was translated"


def test_reference_only_rows_are_kept_not_dropped():
    "A skipped row is still a point a reader may want to look up."
    _need_sudachi()
    entries = ja.library_entries("en")
    skipped = [e for e in entries if e["skipped"]]
    assert skipped, "the Japanese corpus records reference-only rows"
    assert all(e["key"] and e["name"] for e in skipped)


def test_meaning_detailed_is_english_only():
    "The curated long explanation is English prose, so zh/ko omit it."
    _need_sudachi()
    en = ja.library_entries("en")
    zh = ja.library_entries("zh")
    assert any(e.get("meaning_detailed") for e in en)
    assert not any(e.get("meaning_detailed") for e in zh)


def test_related_ids_resolve_and_dangling_ids_are_dropped():
    "Every rendered cross-link targets an existing card; typos vanish."
    _need_sudachi()
    entries = ja.library_entries("en")
    keys = {e["key"] for e in entries}
    related = [rel for e in entries for rel in (e.get("related") or [])]
    assert related, "the corpus carries cross-links"
    assert all(r["key"] in keys for r in related)
    assert all(r["name"] and r["level"] for r in related)
    assert ja._related_entries({"related": ["no-such-entry-id"]}) == []


# ---- panel output (P1) ---------------------------------------------


def test_panel_entry_carries_formality_detail_and_related():
    _need_sudachi()
    entries = ja.analyze_japanese("駅まで歩きます。", "en")
    entry = next(e for e in entries if e["key"] == "ds_particle-made-until")
    assert entry["formality"] == "neutral"
    assert entry["meaning_detailed"]
    assert entry["related"]
    assert entry["related"][0]["key"].startswith("ds_")


def test_panel_entry_omits_english_detail_in_zh_but_keeps_chips():
    _need_sudachi()
    entries = ja.analyze_japanese("駅まで歩きます。", "zh")
    entry = next(e for e in entries if e["key"] == "ds_particle-made-until")
    assert "meaning_detailed" not in entry
    assert entry["formality"] == "neutral"
    assert entry["related"]


# ---- route helpers --------------------------------------------------


def test_level_class_maps_to_the_card_token():
    assert _level_class("N5") == "N5"
    assert _level_class("N1") == "N1"
    # TOPIK bands ride their own token/accent (the raw strings carry a
    # space, so the class names do not); anything else stays neutral.
    assert _level_class("TOPIK 1-2") == "TOPIK12"
    assert _level_class("TOPIK 3-4") == "TOPIK34"
    assert _level_class("TOPIK 5-6") == "TOPIK56"
    assert _level_class("XX") == "N"


def test_level_counts_split_matchable_and_reference():
    counts = _level_counts(
        [
            {"level": "N5", "skipped": False},
            {"level": "N5", "skipped": True},
            {"level": "N4", "skipped": False},
        ]
    )
    # Levels keep the entries' own order, not alphabetical.
    assert list(counts) == ["N5", "N4"]
    assert counts["N5"] == {"total": 2, "matchable": 1, "reference": 1}
    assert counts["N4"] == {"total": 1, "matchable": 1, "reference": 0}


# ---- routes ---------------------------------------------------------


def test_index_lists_languages_with_a_corpus(
    japanese, korean, generic, app_context, client
):
    """
    ?pick=1 opens the list of languages with a corpus (the fallback when
    the current language has none); a corpus-less language is not listed.
    """
    resp = client.get("/grammar/?pick=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Japanese" in body
    assert "Korean" in body
    # A language served by the generic regex rules has no browsable list.
    assert 'href="/grammar/%d"' % generic.id not in body


def _set_current_language(lang_id):
    "Point the user setting at a language, as the settings page would."
    from lute.models.repositories import (
        UserSettingRepository,
    )  # pylint: disable=import-outside-toplevel

    repo = UserSettingRepository(db.session)
    repo.set_value("current_language_id", lang_id)
    db.session.commit()


def test_index_redirects_to_the_current_language(
    japanese, korean, generic, app_context, client
):
    """
    /grammar lands on the language the user is studying -- the reader
    already picked it when they started reading, so no chooser page.
    """
    _set_current_language(japanese.id)
    resp = client.get("/grammar/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/grammar/{japanese.id}")


def test_index_falls_back_to_the_list_when_current_language_has_no_corpus(
    generic, app_context, client
):
    """
    Only Japanese/Korean carry a browsable corpus; any other current
    language still gets the language list instead of a redirect loop.
    """
    _set_current_language(generic.id)
    resp = client.get("/grammar/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Japanese" not in body
    assert "Korean" not in body


def _ja_entries():
    _need_sudachi()
    return ja.library_entries("en")


def _cards_of(body, level_entries):
    "The rendered compact cards must number exactly the level's entries."
    assert body.count('class="grammar-library__card"') == len(level_entries)


def test_library_index_shows_every_level_as_compact_cards(
    japanese, app_context, client
):
    """
    The whole corpus renders on one full-width page: every level as its
    own section of compact cards, the sticky toolbar carrying the
    cross-level search and the level jump.
    """
    entries = _ja_entries()
    body = client.get(f"/grammar/{japanese.id}").get_data(as_text=True)

    # Page chrome: search box, switch-language link, one section per level.
    assert 'id="grammar-library-search"' in body
    assert "grammar-library__toolbar" in body
    assert "grammar-library__switch" in body
    for level in ("N5", "N4", "N3", "N2", "N1"):
        assert f'id="level-{level}"' in body
        assert f'href="#level-{level}"' in body

    # Every level's cards, not just the first level's.
    _cards_of(body, entries)
    assert len(entries) > len([e for e in entries if e["level"] == "N5"])

    # Cards are compact, not the full detail card (no formation/examples
    # inline), and they link to the detail route.
    assert "grammar-item__formation" not in body
    assert 'class="grammar-library__card" href="/grammar/' in body

    # No server-side level switching left: all pills are plain anchors.
    assert "level=N4" not in body
    assert "is-active" not in body

    # No progress/"studied" filter scaffolding slipped in.
    assert "grammar-library__usage" not in body
    assert "★" not in body


def test_library_index_ignores_a_legacy_level_param(japanese, app_context, client):
    "?level= predates the all-levels page; it is accepted and ignored."
    entries = _ja_entries()
    body = client.get(f"/grammar/{japanese.id}?level=N1").get_data(as_text=True)
    _cards_of(body, entries)


def test_detail_page_renders_the_full_card_and_navigation(
    japanese, app_context, client
):
    """
    The card the index hides lives on the point's own page, with a back
    link to the whole library and prev/next to walk its level.
    """
    entries = _ja_entries()
    entry = next(e for e in entries if e["key"] == "ds_particle-made-until")
    body = client.get(f"/grammar/{japanese.id}/{entry['key']}").get_data(as_text=True)

    assert "grammar-item__formation" in body
    assert "grammar-item__examples" in body
    assert "grammar-item__related" in body
    assert "grammar-detail__position" in body
    # The back link goes to the (now all-levels) library, not a level view.
    assert f'href="/grammar/{japanese.id}"' in body
    assert "grammar-detail__prev" in body or "grammar-detail__next" in body
    # The cross-link is the other point's page, not a dead in-page anchor.
    assert "grammar-item__related-link" in body
    rel = entry["related"][0]
    assert f'/grammar/{japanese.id}/{rel["key"]}' in body


def test_detail_unknown_key_redirects_to_the_index(japanese, app_context, client):
    resp = client.get(f"/grammar/{japanese.id}/no-such-key")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/grammar/{japanese.id}")


def test_detail_for_a_corpusless_language_redirects(generic, app_context, client):
    assert client.get(f"/grammar/{generic.id}/anything").status_code == 302


def test_corpusless_language_explains_instead_of_404(generic, app_context, client):
    resp = client.get(f"/grammar/{generic.id}")
    assert resp.status_code == 200
    assert "no grammar library" in resp.get_data(as_text=True).lower()


def test_korean_language_page_renders_its_library(korean, app_context, client):
    "The Korean corpus renders too -- every TOPIK band on the one page."
    pytest.importorskip("kiwipiepy")
    from lute.read.render import (
        grammar_analysis_ko as ko,
    )  # pylint: disable=import-outside-toplevel

    entries = ko.library_entries("en")
    body = client.get(f"/grammar/{korean.id}").get_data(as_text=True)
    _cards_of(body, entries)
    assert "TOPIK 1-2" in body
    assert 'id="level-TOPIK 1-2"' in body


def test_russian_language_page_renders_its_library(russian, app_context, client):
    "The Russian corpus renders on CEFR sections, cards matching entries."
    entries = _ru_entries("en")
    body = client.get(f"/grammar/{russian.id}").get_data(as_text=True)
    _cards_of(body, entries)
    assert "A1" in body
    assert 'id="level-A1"' in body
    # A data row's formation line reaches its detail card.
    formed = next(e for e in entries if e.get("formation"))
    detail = client.get(f"/grammar/{russian.id}/{formed['key']}").get_data(as_text=True)
    assert "grammar-item__formation" in detail


def test_spanish_language_page_renders_its_library(spanish, app_context, client):
    "The Spanish rule set renders too, golden examples and gloss on the cards."
    entries = _es_entries("en")
    body = client.get(f"/grammar/{spanish.id}").get_data(as_text=True)
    _cards_of(body, entries)
    first = next(e for e in entries if e["examples"])
    detail = client.get(f"/grammar/{spanish.id}/{first['key']}").get_data(as_text=True)
    row = first["examples"][0]
    assert row["text"] in detail
    # The gloss sits in its own span, not glued to the sentence.
    assert "grammar-item__example-tr" in detail
    assert str(markupsafe.escape(row["tr"])) in detail


def test_french_language_page_renders_its_library(french, app_context, client):
    "The French rule set renders too, golden examples and gloss on the cards."
    entries = _fr_entries("en")
    body = client.get(f"/grammar/{french.id}").get_data(as_text=True)
    _cards_of(body, entries)
    first = next(e for e in entries if e["examples"])
    detail = client.get(f"/grammar/{french.id}/{first['key']}").get_data(as_text=True)
    row = first["examples"][0]
    assert row["text"] in detail
    assert "grammar-item__example-tr" in detail
    assert str(markupsafe.escape(row["tr"])) in detail


_RULESET_LANGUAGES = (
    ("english", "grammar_analysis_en"),
    ("mandarin", "grammar_analysis_zh"),
    ("cantonese", "grammar_analysis_yue"),
    ("arabic", "grammar_analysis_ar"),
    ("italian", "grammar_analysis_it"),
    ("portuguese", "grammar_analysis_pt"),
)


@pytest.mark.parametrize("fixture_name,module_name", _RULESET_LANGUAGES)
def test_ruleset_language_page_renders_its_library(
    request, fixture_name, module_name, app_context, client
):
    """
    The six rule-set-only languages render the same page shape as the
    data-rich corpora: one compact card per rule, a detail page behind
    each, and the language listed on the pick page.

    No parser is needed for any of this -- the engines' tokenizers load
    lazily, so the library renders on an install without them.
    """
    language = request.getfixturevalue(fixture_name)
    module, _ = _ruleset_engine(module_name)
    entries = module.library_entries("en")
    body = client.get(f"/grammar/{language.id}").get_data(as_text=True)
    _cards_of(body, entries)
    assert f'href="/grammar/{language.id}"' in client.get(
        "/grammar/?pick=1"
    ).get_data(as_text=True)

    # A card opens its point's page: description in, examples out (these
    # corpora carry none, and the template hides the block).
    first = entries[0]
    detail = client.get(f"/grammar/{language.id}/{first['key']}").get_data(as_text=True)
    assert "grammar-item__desc" in detail
    assert "grammar-item__examples" not in detail


def test_korean_page_shows_korean_descriptions(spanish, app_context, client):
    """
    The page resolves each description through the display language, so the
    Korean reader sees the Korean heading above the Korean example line --
    not an English heading above Korean prose.
    """
    spanish.grammar_translate_lang = "ko"
    db.session.commit()
    entries = _es_entries("ko")
    english = {e["key"]: e["desc"] for e in _es_entries("en")}
    first = next(e for e in entries if e["examples"])
    body = client.get(f"/grammar/{spanish.id}/{first['key']}").get_data(as_text=True)
    assert str(markupsafe.escape(first["desc"])) in body
    assert str(markupsafe.escape(english[first["key"]])) not in body
    assert str(markupsafe.escape(first["examples"][0]["tr"])) in body


def test_unknown_language_is_404(client):
    assert client.get("/grammar/999999").status_code == 404


def test_reading_page_links_into_the_library(japanese, app_context, client):
    """
    Both entry points live on the learning surface, not the settings pages:
    the reading menu item (server-rendered) and the panel header link
    (rendered by JS, so only its data source -- the lang_id input -- is
    pinned here).
    """
    book = make_book("Grammar library entry", "たのしい ひさしぶり", japanese)
    db.session.add(book)
    db.session.commit()

    body = client.get(f"/read/{book.id}").get_data(as_text=True)
    assert f'href="/grammar/{japanese.id}"' in body
    assert 'id="lang_id"' in body
