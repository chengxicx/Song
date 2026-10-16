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
    The whole corpus renders on one full-width page (hanabira.org
    style): every level as its own section of compact cards, the sticky
    toolbar carrying the cross-level search and the level jump.
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
