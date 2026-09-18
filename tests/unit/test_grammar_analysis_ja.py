"""Tests for the N5 Japanese grammar-analysis engine."""

import re

import pytest

from lute.read.render.grammar_analysis_ja import (
    _ALL_LEVELS,
    _ALL_RULES,
    _DATA_RULES,
    _FUNCTION_WORD_IDS,
    _N5_RULES,
    _PARTICLE_IDS,
    _ZH_DESC,
    analyze_japanese,
)
from lute.read.render.grammar_analysis import is_japanese_language


@pytest.fixture(name="rules")
def rules_fixture():
    return {r["key"]: r for r in _N5_RULES}


def _keys(text):
    return {e["key"] for e in analyze_japanese(text)}


def test_english_regex_demo_unchanged():
    "The original English regex engine should still exist and work on English."
    from lute.read.render.grammar_analysis import analyze

    result = analyze(["I used to play tennis."])
    assert any("used to" in r["name"] for r in result)


def test_n5_rules_each_have_data(rules):
    "Every N5 rule carries a level, meaning, and at least one example."
    for rule in rules.values():
        assert rule["level"] == "N5"
        assert rule["meaning"]
        assert rule["examples"]


def test_each_rule_matches_its_own_examples(rules):
    "A rule must fire on its OpenJLPT example sentences."
    for key, rule in rules.items():
        text = "".join(rule["examples"])
        hits = _keys(text)
        if rule.get("kind") == "particle":
            # Pure particles are folded into one aggregated entry.
            assert "basic_particles" in hits, f"particle rule {key} not aggregated"
        else:
            assert key in hits, f"rule {key} did not match its own examples"


def test_particles_are_aggregated():
    "Pure particles (を/に/で/と) collapse into a single trailing entry."
    results = analyze_japanese("本を読みます。学校で勉強します。7時に起きます。")
    keys = [e["key"] for e in results]
    assert "wo_object" not in keys
    assert "de_place_means" not in keys
    assert "ni_time_destination" not in keys
    assert "basic_particles" in keys
    particles_entry = next(e for e in results if e["key"] == "basic_particles")
    assert particles_entry["name"] == "Particles: で・に・を"
    assert particles_entry["examples"]
    # Aggregated entry is trailing (lowest priority).
    assert keys[-1] == "basic_particles"


def test_particles_still_detected_when_alone():
    "Even a lone particle hit still yields a Particles entry."
    results = analyze_japanese("本を読みます。")
    assert any(e["key"] == "basic_particles" for e in results)


@pytest.mark.parametrize(
    "key, sentence",
    [
        # A fixed particle/conjugation should NOT be reported by an unrelated rule.
        ("te_iru", "窓を開けました。"),  # past, no progressive ている
        ("te_kudasai", "窓を開けました。"),  # no ください
        ("tai", "これはたいへんです。"),  # たいへん, not desire 〜たい
        ("ta_koto_ga_arimasu", "日本に行きました。"),  # no こと
        ("mashou", "一緒に行きます。"),  # plain ます, not ましょう
        ("masen_ka", "一緒に行きましょう。"),  # ましょう, not ませんか
        ("te_wa_ikemasen", "写真を撮ってもいいです。"),  # permission, not prohibition
        ("te_mo_ii_desu", "ここで写真を撮ってはいけません。"),  # prohibition, not permission
    ],
)
def test_negative_examples_do_not_false_positive(key, sentence):
    "Sentences that lack a construction must not report that rule."
    hits = _keys(sentence)
    assert key not in hits, f"rule {key} should NOT match: {sentence}"


def test_subtitle_line_ending_in_match_does_not_overflow():
    """
    A subtitle/transcript line with no trailing 。 may end exactly on a
    matched construction (e.g. "〜から").  The token run then reaches the
    end of the token list; the character span must clamp to the sentence
    end instead of indexing a non-existent offset.
    """
    results = analyze_japanese("これが一番おいしいですから\nええ 友達が来ますから")
    assert any(e["key"] == "kara_reason" for e in results)
    entry = next(e for e in results if e["key"] == "kara_reason")
    for ex in entry["examples"]:
        s = ex["sentence"]
        for m in ex["matches"]:
            assert m["start"] >= 0 and m["end"] <= len(s), f"match out of range: {m}"
            assert s[m["start"] : m["end"]] == "から"


def test_multiline_example_never_spans_the_line_break():
    """
    A logical sentence split by a hard line break (a newline in the raw
    text becomes a paragraph break on the reading page) must not be
    returned as one multi-line example: the front-end locates examples
    against individual sentence nodes, and a newline-bearing example would
    make it ring several unrelated sentences (e.g. 〜によって circling the
    previous sentence too).
    """
    results = analyze_japanese(
        "冬は十二月から二月ごろまでで、北の地方では雪がたくさん降ります。\n"
        "季節によって食べ物や行事も変わる\n"
        "ので、日本の生活はとても楽しいです。"
    )
    entry = next(e for e in results if "よっ" in e["name"])
    for ex in entry["examples"]:
        assert "\n" not in ex["sentence"], f"example spans a line break: {ex['sentence']!r}"
    assert any(
        ex["sentence"] == "季節によって食べ物や行事も変わる" for ex in entry["examples"]
    )


def test_analysis_return_shape():
    "Return entries have the fields the front-end panel renders."
    results = analyze_japanese("日本に行ったことがあります。")
    entry = next(e for e in results if e["key"] == "ta_koto_ga_arimasu")
    assert entry["level"] == "N5"
    assert entry["name"]
    assert entry["desc"]
    assert entry["examples"][0]["sentence"]


def test_is_japanese_language_detection():
    "Language detection routes Japanese books to the Sudachi engine."

    class FakeLang:
        def __init__(self, parser_type, name):
            self.parser_type = parser_type
            self.name = name

    assert is_japanese_language(FakeLang("japanese", "Japanese")) is True
    assert is_japanese_language(FakeLang("japanese_sudachi", "日本語")) is True
    assert is_japanese_language(FakeLang("spacedel", "Japanese")) is True  # by name
    assert is_japanese_language(FakeLang("spacedel", "Spanish")) is False
    assert is_japanese_language(None) is False


def test_data_rules_loaded_for_all_levels():
    "Data-driven rules are loaded for N5-N1 with the needed fields."
    for level in _ALL_LEVELS:
        rules = [r for r in _DATA_RULES if r["level"] == level]
        assert rules, f"no data rules loaded for {level}"
        for r in rules:
            assert r["level"] == level
            assert r["meaning"]
            assert r["examples"]


def test_full_jlpt_library_is_loaded():
    "The whole curated JLPT grammar library is present, not just a sample."
    assert len(_DATA_RULES) >= 590
    by_level = {lvl: len([r for r in _DATA_RULES if r["level"] == lvl]) for lvl in _ALL_LEVELS}
    assert by_level == {"N5": 77, "N4": 89, "N3": 130, "N2": 149, "N1": 150}, by_level
    # The great majority of entries must be usable matchers rather than
    # skipped: entries only get skipped for good reason (see _load_level).
    active = [r for r in _DATA_RULES if not r["skipped"]]
    assert len(active) >= 480, f"only {len(active)} of {len(_DATA_RULES)} rules are active"


def test_each_data_rule_matches_its_own_examples():
    """
    Every non-skipped data rule fires on a text built from its own examples.
    Function-word and particle rules are folded into the aggregated entries,
    so those are checked against the aggregate they land in.

    Rules that share a headline are folded into one row (〜がほしい and
    〜てほしい both report as 〜ほしい), and the surviving row carries the
    first rule's key -- so a construction is looked up by name.
    """
    active = [r for r in _DATA_RULES if not r["skipped"]]
    assert active, "expected some active data rules"
    for rule in active:
        text = "".join(rule["examples"])
        entries = analyze_japanese(text)
        hits = {e["key"] for e in entries}
        names = {e["name"] for e in entries}
        if rule["kind"] == "basic":
            assert "basic_forms" in hits, f"data rule {rule['key']} not folded into basic_forms"
        elif rule["kind"] == "particle":
            assert "basic_particles" in hits, f"data rule {rule['key']} not folded into basic_particles"
        else:
            assert rule["key"] in hits or rule["pattern"] in names, (
                f"data rule {rule['key']} ({rule['pattern']}) did not match its own examples"
            )


def test_data_rule_kinds_are_known():
    """
    A data rule reports on its own unless it is on one of the two reviewed
    lists -- function words (folded into the copula row) or particle usages
    (folded into the particle row).  Nothing is folded away by accident.
    """
    folded = {"ds_" + i for i in _FUNCTION_WORD_IDS | _PARTICLE_IDS}
    for rule in _DATA_RULES:
        if rule["key"] in folded:
            continue
        assert rule.get("kind") == "construction", rule["key"]


def test_copula_and_demonstratives_are_aggregated_not_listed():
    """
    です / ます and friends appear in most sentences and teach no grammar point
    of their own, so they are reported as one capped entry instead of one entry
    per form.
    """
    results = analyze_japanese("私は学生です。毎日本を読みます。", display_lang="zh")
    keys = [e["key"] for e in results]
    assert "basic_forms" in keys
    assert not [k for k in keys if k.startswith("ds_") and "desu" in k]
    basics = next(e for e in results if e["key"] == "basic_forms")
    assert basics["name"].startswith("Basic forms: ")
    assert "です" in basics["name"]


def test_only_reviewed_function_words_are_aggregated():
    """
    The aggregated copula row holds exactly the reviewed function-word ids.
    Nothing is folded away because its pattern happens to be short kana: a
    heuristic on the text cannot tell のに / ばかり / ほど from です, and
    getting it wrong buries real grammar points (it used to bury 132 of them).
    """
    aggregated = {r["key"] for r in _DATA_RULES if r.get("kind") == "basic"}
    assert aggregated == {"ds_" + i for i in _FUNCTION_WORD_IDS}
    assert len(aggregated) == 12


@pytest.mark.parametrize(
    "sentence, key",
    [
        ("一生懸命勉強したのに、試験に落ちてしまった。", "ds_noni-although"),
        ("今日は死ぬほど疲れた。", "ds_hodo-extent"),
        ("来年、日本に行くつもりです。", "ds_tsumori-intention"),
    ],
)
def test_short_kana_grammar_points_report_on_their_own(sentence, key):
    "A short kana tail is not a reason to hide a grammar point from the panel."
    assert key in _keys(sentence)


@pytest.mark.parametrize(
    "sentence, expected",
    [
        # A real quotative って after a plain form.
        ("明日行くって言った。", True),
        # The って of a verb's て-form is that verb's conjugation, not the
        # quotative -- this is what a kana-only literal would otherwise hit.
        ("みんな困っているよ。", False),
        ("本を読んでいます。", False),
    ],
)
def test_literal_must_not_start_inside_a_verb(sentence, expected):
    """
    って is a kana-only literal, so it would otherwise be found inside any
    て-form (困っている -> っ + て).  A literal may not begin in the middle of a
    verb token, while starting mid-token stays fine elsewhere -- the くありま
    せん of 面白くありません begins inside an adjective.

    Unfiltered this rule hit 309 of 3982 corpus sentences and every sample was
    a て-form; with the guard it hits 1, and the quotative still matches.
    """
    keys = _keys(sentence)
    assert ("ds_tte-quotation" in keys) is expected, keys


def test_chinese_display_language():
    "display_lang='zh' yields the curated Chinese gloss; default stays English."
    zh = analyze_japanese("毎日運動することにした。", display_lang="zh")
    hit = next(e for e in zh if e["key"] == "ds_koto-ni-suru-decide")
    assert re.search(r"[\u4e00-\u9fff]", hit["desc"]), (
        f"expected Chinese desc, got: {hit['desc']!r}"
    )
    assert not hit["desc"].isascii()

    en = analyze_japanese("毎日運動することにした。")
    en_hit = next(e for e in en if e["key"] == "ds_koto-ni-suru-decide")
    rule = next(r for r in _ALL_RULES if r["key"] == "ds_koto-ni-suru-decide")
    assert en_hit["desc"] == rule["meaning"]
    assert "decide" in rule["meaning"]


def test_zh_glosses_cover_the_whole_library():
    "Every data rule carries a Chinese gloss (zh.json covers every entry id)."
    missing = [r["key"] for r in _DATA_RULES if not r.get("meaning_zh")]
    assert missing == [], f"{len(missing)} rules without a Chinese gloss: {missing[:5]}"
    for rule in _DATA_RULES:
        assert re.search(r"[\u4e00-\u9fff]", rule["meaning_zh"]), rule["key"]


def test_zh_table_still_covers_hand_written_rules():
    "The legacy hand-written N5 table is still complete for its own rules."
    assert _ZH_DESC, "translation table must not be empty"
    hand_written = [r for r in _ALL_RULES if r["key"] in {n["key"] for n in _N5_RULES}]
    missing = [r["pattern"] for r in hand_written if r["pattern"] not in _ZH_DESC]
    assert missing == []
    for value in _ZH_DESC.values():
        assert re.search(r"[\u4e00-\u9fff]", value), f"non-Chinese desc: {value!r}"


# ---- regression tests for the full-library matching rules ----------------


def test_reported_sentence_now_matches_grammar():
    """
    私はどれほど驚いたでしょう (book 271) matched nothing when only the
    hand-written N5 rules existed: は was not a rule, and neither ほど nor
    でしょう was in the library.  でしょう is an N4 point (だろう / でしょう),
    so it must be reported now.
    """
    results = analyze_japanese("私はどれほど驚いたでしょう。", display_lang="zh")
    assert "ds_darou-deshou-conjecture" in {e["key"] for e in results}
    hit = next(e for e in results if e["key"] == "ds_darou-deshou-conjecture")
    assert hit["level"] == "N4"
    ex = hit["examples"][0]
    assert ex["sentence"] == "私はどれほど驚いたでしょう。"
    for m in ex["matches"]:
        assert ex["sentence"][m["start"] : m["end"]] == "でしょう"


def test_same_name_rows_are_merged():
    """
    Several rules can carry one surface form (から as "because" and 〜てから as
    "after doing"), and a bare literal cannot tell which one a given から is.
    The panel must show one row per name with the glosses joined rather than
    the same から twice, and never two rows with the same name.
    """
    results = analyze_japanese("食べてから、行きます。寒いから、家にいます。", display_lang="zh")
    names = [e["name"] for e in results]
    assert len(names) == len(set(names)), names

    kara = [e for e in results if e["name"] == "〜から"]
    assert len(kara) == 1, kara
    assert "因为" in kara[0]["desc"] and "之后" in kara[0]["desc"], kara[0]["desc"]


def test_the_reported_sentence_reports_hodo_not_basics():
    """
    ほど is an N3 grammar point, not a "basic form": it must be reported under
    its own key rather than folded into the aggregated row, which is exactly
    the complaint that started this -- the reported sentence showed nothing
    useful because ほど, でしょう and the rest were all hidden.
    """
    keys = _keys("私はどれほど驚いたでしょう。")
    assert "ds_hodo-extent" in keys
    assert "basic_forms" not in keys


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("ここで食べてもいいですか。", "ds_te-mo-ii-permission"),
        ("ここで写真を撮ってはいけません。", None),
        ("期限などどうでもいいです。", None),  # どうでもいい is not 〜てもいい
    ],
)
def test_te_form_prefix_constraint(sentence, expected):
    """
    A "Verb-て + X" entry must really see a verb + て in front of X: the で of
    どうでもいい must not be mistaken for a て form.
    """
    keys = _keys(sentence)
    if expected:
        assert expected in keys, f"expected {expected} for {sentence}"
    else:
        assert "ds_te-mo-ii-permission" not in keys, f"false positive on {sentence}"


def test_gapped_construction_needs_both_anchors():
    "〜から...にかけて (a gapped point) fires only when both anchors appear."
    with_anchors = _keys("三月から五月にかけて花が咲きます。")
    assert "ds_ni-kakete-through" in with_anchors
    only_tail = _keys("五月にかけて花が咲きます。")
    assert "ds_ni-kakete-through" not in only_tail


def test_regex_specs_only_match_whole_tokens():
    """
    The literal 上に must not be found inside the single word 地上, which is
    a different word entirely (N2 〜上に means "besides / on top of that").
    """
    assert "ds_ue-ni-in-addition" not in _keys("完璧なウインクで地上に星を飛ばす。")


def test_plain_form_marker_keeps_compound_nouns_out():
    "いすの上に is the ordinary noun + の, not the N2 〜上に."
    assert "ds_ue-ni-in-addition" not in _keys("いすの上に置いてください。")
    assert "ds_ue-ni-in-addition" in _keys("安い上に、便利です。")


def test_la_lemma_chain_does_not_leak_from_mashou():
    """
    Sudachi lemmatises ましょう to ます, so a lemma-based spec for 〜ましょうか
    also matches a plain ますか question.  The spec must use surfaces.
    """
    assert "ds_mashou-ka-invitation" not in _keys("毎晩何を飲みますか。")
    assert "ds_mashou-ka-invitation" in _keys("一緒に飲みましょうか。")


def test_skipped_rules_never_fire():
    "Rules marked skipped carry no matcher and can never appear."
    skipped = [r for r in _DATA_RULES if r["skipped"]]
    assert skipped, "the loader should skip the unusable entries"
    for rule in skipped:
        assert rule["patterns"] == []
        text = "".join(rule["examples"])
        assert rule["key"] not in _keys(text)


def test_examples_are_capped_per_rule():
    """
    A common construction appears many times on a page; the panel keeps a few
    anchor examples per point instead of every instance.
    """
    from lute.read.render.grammar_analysis_ja import _CONSTRUCTION_EXAMPLE_CAP

    results = analyze_japanese(
        "寿司を食べています。ご飯を食べています。パンを食べています。"
        "そばを食べています。うどんを食べています。",
        display_lang="zh",
    )
    entry = next(e for e in results if e["key"] == "te_iru")
    assert len(entry["examples"]) == _CONSTRUCTION_EXAMPLE_CAP


def test_aggregated_entries_come_last():
    "Basics and Particles aggregates are trailing so real points lead."
    keys = [e["key"] for e in analyze_japanese("私は学生です。本を読みます。", display_lang="zh")]
    assert keys[-1] == "basic_particles"
    assert keys[-2] == "basic_forms"
