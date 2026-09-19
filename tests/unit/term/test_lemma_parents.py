"""
Tests for the lemma/parent backfill rules (roadmap 2.3).

The rules are pure -- the parser is injected -- so no sudachi dictionary
is needed here.  See tests/unit/cli/test_term_parent_backfill.py for what
the tool actually writes to the database.
"""

from lute.term.lemma_parents import (
    POLICY_SINGLE_CONTENT_WORD,
    REASON_CONCATENATED,
    REASON_HAS_PARENT,
    REASON_KANA,
    REASON_MULTI_TOKEN,
    REASON_NO_LEMMA,
    resolve_lemma_root,
    select_links,
    would_cycle,
)

ZWS = "\u200B"

# A stand-in for get_lemma(): the parser strips the zero-width spaces.
LEMMAS = {
    "食べた": "食べる",
    "食べ": "食べる",
    "入りました": "入る",
    "似ている": "似るいる",
    "間もなく": "間ない",
}

# A stand-in for content_token_count().
CONTENT_WORDS = {
    "食べた": 1,
    "食べ": 1,
    "入りました": 1,
    "似ている": 2,
    "間もなく": 2,
}


def _lemma_of(text):
    "Dictionary form, or None."
    return LEMMAS.get(text.replace(ZWS, ""))


def _content_words(text):
    "Content word count."
    return CONTENT_WORDS.get(text.replace(ZWS, ""), 1)


def test_single_content_word_is_linked():
    "The happy path: 食べた -> 食べる."
    plan = select_links([(1, "食べた", 1)], _lemma_of, _content_words, set())
    assert plan.total_terms == 1
    assert [
        (lnk.child_id, lnk.child_text, lnk.lemma, lnk.child_status)
        for lnk in plan.links
    ] == [(1, "食べた", "食べる", 1)]
    assert plan.skipped == {}


def test_all_hiragana_is_skipped_before_the_parser_is_asked():
    "Hiragana has no dictionary form; counted, not silently dropped."
    asked = []

    def lemma_of(text):
        asked.append(text)
        return _lemma_of(text)

    plan = select_links([(1, "たべた", 0)], lemma_of, _content_words, set())
    assert not plan.links
    assert plan.skipped[REASON_KANA] == 1
    assert asked == []


def test_multi_token_term_is_skipped_by_default():
    "The task doc's constraint: single-token terms only."
    text = "入り" + ZWS + "ました"
    plan = select_links([(1, text, 1)], _lemma_of, _content_words, set())
    assert not plan.links
    assert plan.skipped[REASON_MULTI_TOKEN] == 1


def test_single_content_word_phrase_can_be_linked_when_allowed():
    "入りました is three tokens and one content word: a real dictionary form."
    text = "入り" + ZWS + "ました"
    plan = select_links(
        [(1, text, 1)],
        _lemma_of,
        _content_words,
        set(),
        POLICY_SINGLE_CONTENT_WORD,
    )
    assert [lnk.lemma for lnk in plan.links] == ["入る"]


def test_concatenated_phrase_is_refused_under_both_policies():
    "似ている -> 似るいる is not a term anybody wants."
    text = "似て" + ZWS + "いる"
    for policy in ("skip", POLICY_SINGLE_CONTENT_WORD):
        plan = select_links([(1, text, 0)], _lemma_of, _content_words, set(), policy)
        assert not plan.links
        assert plan.skipped[REASON_MULTI_TOKEN] == 1

    plan = select_links([(1, "似ている", 0)], _lemma_of, _content_words, set())
    assert not plan.links
    assert plan.skipped[REASON_CONCATENATED] == 1
    assert plan.skipped_samples[REASON_CONCATENATED] == ["似ている -> 似るいる"]


def test_term_without_a_dictionary_form_is_skipped():
    plan = select_links([(1, "東京", 0)], _lemma_of, _content_words, set())
    assert not plan.links
    assert plan.skipped[REASON_NO_LEMMA] == 1


def test_a_lemma_equal_to_the_term_is_not_a_parent():
    "get_lemma returning the text itself means 'already the dictionary form'."
    plan = select_links([(1, "東京", 0)], lambda t: t, _content_words, set())
    assert not plan.links
    assert plan.skipped[REASON_NO_LEMMA] == 1


def test_a_multi_token_term_equal_to_its_lemma_is_not_linked():
    "The zero-width spaces are stripped before comparing, as find_or_new does."
    text = "入り" + ZWS + "ました"
    plan = select_links(
        [(1, text, 1)],
        lambda t: t.replace(ZWS, ""),
        _content_words,
        set(),
        POLICY_SINGLE_CONTENT_WORD,
    )
    assert not plan.links
    assert plan.skipped[REASON_NO_LEMMA] == 1


def test_term_that_already_has_a_parent_is_left_alone():
    plan = select_links([(1, "食べた", 0)], _lemma_of, _content_words, {1})
    assert not plan.links
    assert plan.skipped[REASON_HAS_PARENT] == 1


def test_the_parent_guard_runs_first():
    "Hand-made links are never re-examined, whatever else they look like."
    plan = select_links([(1, "たべた", 0)], _lemma_of, _content_words, {1})
    assert plan.skipped[REASON_HAS_PARENT] == 1
    assert REASON_KANA not in plan.skipped


def test_every_row_is_accounted_for():
    rows = [(1, "食べた", 1), (2, "たべた", 0), (3, "似ている", 0), (4, "東京", 0)]
    plan = select_links(rows, _lemma_of, _content_words, set())
    assert plan.total_terms == 4
    assert len(plan.links) == 1
    assert plan.skipped_total() == 3


def test_skip_samples_are_capped():
    rows = [(i, "似ている", 0) for i in range(100)]
    plan = select_links(rows, _lemma_of, _content_words, set())
    assert plan.skipped[REASON_CONCATENATED] == 100
    assert len(plan.skipped_samples[REASON_CONCATENATED]) == 8


def test_resolve_lemma_root_follows_the_chain():
    "Sudachi maps a single kanji to an inflected entry: 在 -> 在り -> 在る."
    lemmas = {"在": "在り", "在り": "在る"}
    root, chain = resolve_lemma_root(lemmas.get, "在")
    assert root == "在る"
    assert chain == ["在り", "在る"]


def test_resolve_lemma_root_stops_at_a_fixed_point():
    "食べた -> 食べる, and 食べる has no lemma of its own."
    lemmas = {"食べた": "食べる"}
    assert resolve_lemma_root(lemmas.get, "食べた") == ("食べる", ["食べる"])


def test_the_chain_root_is_used_as_the_parent():
    "The intermediate term (在り) is not created at all."
    lemmas = {"在": "在り", "在り": "在る"}
    plan = select_links([(1, "在", 0)], lemmas.get, lambda _t: 1, set())
    assert [lnk.lemma for lnk in plan.links] == ["在る"]
    assert plan.chained_lemma_count == 1
    assert plan.chained_samples == ["在 -> 在り -> 在る"]


def test_a_new_root_is_not_a_cycle():
    assert would_cycle(1, 2, lambda _i: None) is False


def test_a_link_back_to_an_existing_child_is_a_cycle():
    "2 -> 1 exists, so 1 -> 2 has to be refused."
    parents = {2: 1}
    assert would_cycle(1, 2, parents.get) is True


def test_a_self_link_is_a_cycle():
    assert would_cycle(7, 7, lambda _i: None) is True


def test_a_chain_deeper_than_the_limit_is_refused():
    "Lemma chains are short; anything longer is not something to guess at."
    parents = {i: i + 1 for i in range(1, 30)}
    assert would_cycle(1, 2, parents.get) is True
