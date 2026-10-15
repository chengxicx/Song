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

import pytest

from lute.db import db
from lute.grammar.routes import _level_class, _level_counts
from lute.read.render import grammar_analysis_ja as ja
from tests.utils import make_book

_JA_RANK = {"N5": 0, "N4": 1, "N3": 2, "N2": 3, "N1": 4}
_KO_RANK = {"TOPIK 1-2": 0, "TOPIK 3-4": 1, "TOPIK 5-6": 2}


def _need_sudachi():
    "The Japanese corpus is only loaded when Sudachi is installed."
    pytest.importorskip("sudachipy")


def _ko_entries(display_lang="en"):
    "library_entries() for Korean, skipping when kiwipiepy is absent."
    ko = pytest.importorskip("lute.read.render.grammar_analysis_ko")
    return ko.library_entries(display_lang)


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
    # TOPIK bands and any other level fall back to the neutral accent.
    assert _level_class("TOPIK 1-2") == "N"


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


def test_index_lists_languages_with_a_corpus(japanese, korean, generic, app_context, client):
    resp = client.get("/grammar/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Japanese" in body
    assert "Korean" in body
    # A language served by the generic regex rules has no browsable list.
    assert 'href="/grammar/%d"' % generic.id not in body


def test_language_page_renders_the_library(japanese, app_context, client):
    "The library page carries its chrome, inventory bar, cards and anchors."
    resp = client.get(f"/grammar/{japanese.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Page chrome: the search box and the "back to all languages" link.
    assert 'id="grammar-library-search"' in body
    assert 'href="/grammar/"' in body
    # Inventory bar (list, not progress): a level jump link carrying the
    # matchable count, with the reference-only half alongside it.
    assert 'href="#level-N5"' in body
    assert "grammar-library__stat" in body
    assert "grammar-library__stat--quiet" in body
    # A level section and its card, anchored by the entry key so the page
    # supports the panel's #key deep links.
    assert 'id="level-N5"' in body
    assert 'id="ds_' in body
    assert 'data-search="' in body
    # Cross-links inside the page target the other card's anchor.
    assert 'class="grammar-item__related-link" href="#ds_' in body
    # No "studied"/progress filter scaffolding slipped in.
    assert "grammar-library__usage" not in body
    assert "★" not in body



def test_corpusless_language_explains_instead_of_404(generic, app_context, client):
    resp = client.get(f"/grammar/{generic.id}")
    assert resp.status_code == 200
    assert "no grammar library" in resp.get_data(as_text=True).lower()


def test_korean_language_page_renders_its_library(korean, app_context, client):
    "The Korean corpus renders too; TOPIK bands become section anchors."
    pytest.importorskip("kiwipiepy")
    resp = client.get(f"/grammar/{korean.id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "grammar-library__section" in body
    # A level whose name carries a space must still make a valid anchor id.
    assert 'id="level-TOPIK-1-2"' in body


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

