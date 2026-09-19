"""Tests for the N5 Japanese grammar-analysis engine."""

import re

import pytest

pytest.importorskip("sudachipy")
pytest.importorskip("sudachidict_core")

from lute.read.render.grammar_analysis_ja import (
    _ALL_LEVELS,
    _ALL_RULES,
    _CONCEPT_IDS,
    _DATA_RULES,
    _FUNCTION_WORD_IDS,
    _N5_RULES,
    _PARTICLE_IDS,
    _SLOT_SPECS,
    _VOCAB_IDS,
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
        "冬は十二月から二月ごろまでで、北の地方では雪がたくさん降ります。\n" "季節によって食べ物や行事も変わる\n" "ので、日本の生活はとても楽しいです。"
    )
    entry = next(e for e in results if "よっ" in e["name"])
    for ex in entry["examples"]:
        assert (
            "\n" not in ex["sentence"]
        ), f"example spans a line break: {ex['sentence']!r}"
    assert any(ex["sentence"] == "季節によって食べ物や行事も変わる" for ex in entry["examples"])


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
    by_level = {
        lvl: len([r for r in _DATA_RULES if r["level"] == lvl]) for lvl in _ALL_LEVELS
    }
    assert by_level == {"N5": 77, "N4": 89, "N3": 130, "N2": 149, "N1": 150}, by_level
    # The great majority of entries must be usable matchers rather than
    # skipped: entries only get skipped for good reason (see _load_level).
    active = [r for r in _DATA_RULES if not r["skipped"]]
    assert (
        len(active) >= 480
    ), f"only {len(active)} of {len(_DATA_RULES)} rules are active"


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
            assert (
                "basic_forms" in hits
            ), f"data rule {rule['key']} not folded into basic_forms"
        elif rule["kind"] == "particle":
            assert (
                "basic_particles" in hits
            ), f"data rule {rule['key']} not folded into basic_particles"
        else:
            assert (
                rule["key"] in hits or rule["pattern"] in names
            ), f"data rule {rule['key']} ({rule['pattern']}) did not match its own examples"


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
    # A hand-reviewed list, not a growing bucket: if it needs more than a
    # dozen members the classification has drifted back into guessing.
    assert len(aggregated) <= 12
    assert len(aggregated) == len(_FUNCTION_WORD_IDS)


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
    "sentence, key",
    [
        # The *short* alternative of a pattern's fragment list.  The derivation
        # used to iterate only the "distinctive" fragments, so whenever a longer
        # one qualified the shorter one never became a matcher at all -- these
        # forms were silently unreported, not merely mis-titled.
        ("この本は高くないです。", "ds_i-adjective-negative"),
        ("明日は雨だろう。", "ds_darou-deshou-conjecture"),
        ("安いけど、買わない。", "ds_ga-kedo-although"),
    ],
)
def test_every_alternative_in_a_pattern_is_matched(sentence, key):
    """ "くない / くありません" is two forms; both have to match."""
    assert key in _keys(sentence)


def test_short_fragments_affect_the_title_not_the_matchers():
    """
    The kana-length rule picks the row's *title*, never which forms the entry
    teaches.  i-adjective-negative matches both forms but is still titled with
    its distinctive fragment, which is what keeps row names readable.
    """
    rule = next(r for r in _DATA_RULES if r["key"] == "ds_i-adjective-negative")
    assert len(rule["derived"]) == 2, rule["derived"]
    assert rule["pattern"] == "〜くありません"


def test_question_words_are_aggregated_not_listed():
    """
    question-words-basic (何 / 誰 / どこ / いつ / どう / どうして) is the other
    half of the こそあど series that koko-soko-asoko-doko already represents --
    どこ appears in both -- and a question word is what the word popup answers.
    Once the derivation stopped dropping its shorter fragments it reached a
    third of all pages, so it joins the aggregated row rather than getting one.
    """
    for sentence in ("これは何ですか。", "いつ行きますか。", "どこにありますか。"):
        assert "ds_question-words-basic" not in _keys(sentence), sentence
    assert "basic_forms" in _keys("これは何ですか。")


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
    assert re.search(
        r"[\u4e00-\u9fff]", hit["desc"]
    ), f"expected Chinese desc, got: {hit['desc']!r}"
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


@pytest.mark.parametrize(
    "sentence",
    [
        "今、ご飯を食べています。",  # て form
        "今、本を読んでいます。",  # で form (む/ぶ/ぬ/ぐ verbs)
        "東京に住んでいます。",
        "猫が死んでいる。",
        "このお寺は江戸時代に建てられています。",  # passive: て follows an auxiliary
        "くじらは昔、人間に捕まえられていました。",
    ],
)
def test_te_iru_covers_both_te_and_de(sentence):
    """
    〜ている must fire on the で form too: Sudachi reports the て-form of
    む/ぶ/ぬ/ぐ verbs as a separate で token (読ん + で + います), so a rule
    written against て alone silently missed half the verbs.

    Passives are in here for the other half of the same decision: the て of
    言われています is preceded by the 受身 auxiliary, not the verb, so the
    spec must not require a 動詞 immediately in front of it.
    """
    assert "te_iru" in _keys(sentence), sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "本を読んで、います。",  # comma breaks the sequence
        "ここにいます。",  # いる as the main verb, no て form in front
    ],
)
def test_te_iru_does_not_overreach(sentence):
    "Widening 〜ている to で must not make it fire without a preceding て form."
    assert "te_iru" not in _keys(sentence), sentence


def test_existence_and_progressive_are_reported_once():
    """
    〜がいます / 〜があります and 〜ています are the hand-written rules' job.
    Their data entries derive a bare います / あります literal -- which also
    matches the います of 知っています -- so they are superseded rather than
    folded into the basics row, where a stray います reads as an existence
    marker.
    """
    existence = _keys("庭に犬がいます。")
    assert "ga_imasu_arimasu" in existence
    assert "ds_imasu-existence-animate" not in existence

    inanimate = _keys("机の上に本があります。")
    assert "ga_imasu_arimasu" in inanimate
    assert "ds_arimasu-existence-inanimate" not in inanimate

    # ... including where both occur, so the panel shows the point once.
    both = [e for e in analyze_japanese("犬がいます。本があります。") if "います" in e["name"]]
    assert len(both) == 1, [e["name"] for e in both]

    progressive = _keys("今、本を読んでいます。")
    assert "te_iru" in progressive
    assert "ds_te-imasu-progressive" not in progressive

    # 〜ています is not the polite ます -- see _NOT_AFTER -- so it feeds no
    # basic form at all.  It used to, which is what put います in the row's
    # title and in its highlighted examples.
    progressive_entries = analyze_japanese("今、本を読んでいます。")
    assert "basic_forms" not in [e["key"] for e in progressive_entries]

    # The basics row itself names no existence marker.
    basics = [
        e for e in analyze_japanese("私は学生です。毎朝、コーヒーを飲みます。") if e["key"] == "basic_forms"
    ]
    assert basics, "expected at least one basic form on this sentence"
    assert "います" not in basics[0]["name"], basics[0]["name"]


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
        "寿司を食べています。ご飯を食べています。パンを食べています。" "そばを食べています。うどんを食べています。",
        display_lang="zh",
    )
    entry = next(e for e in results if e["key"] == "te_iru")
    assert len(entry["examples"]) == _CONSTRUCTION_EXAMPLE_CAP


def test_aggregated_entries_come_last():
    "Basics and Particles aggregates are trailing so real points lead."
    keys = [e["key"] for e in analyze_japanese("私は学生です。本を読みます。", display_lang="zh")]
    assert keys[-1] == "basic_particles"
    assert keys[-2] == "basic_forms"


# --- reviewed vocabulary entries (see _VOCAB_IDS) --------------------------
#
# A row reading "〜時間 = ……小时" costs a row and teaches no grammar: the word
# popup already answers that.  Which entries those are is reviewed by hand;
# scripts/screen_grammar_library.py produces the candidates.

_VOCAB_SENTENCES = [
    ("三時間歩いた。", "ds_jikan-time-duration"),
    ("友だちと一緒に行きます。", "ds_issho-ni-together"),
    ("一番高い山に登った。", "ds_ichiban-superlative"),
    ("毎週映画を見ます。", "ds_mai-every-prefix"),
    ("何時に起きますか。", "ds_nanji-what-time"),
    ("今日は何曜日ですか。", "ds_nanyoubi-day-of-week"),
]


@pytest.mark.parametrize("sentence,key", _VOCAB_SENTENCES)
def test_vocabulary_entries_never_get_a_row(sentence, key):
    "A reviewed vocabulary entry is silent even where its word occurs."
    assert key not in _keys(sentence)


def test_vocabulary_entries_keep_their_derived_spec():
    """
    These are skipped on review, not because their pattern yielded nothing:
    "derived" keeps what the screen matched on, so the verdict can be
    re-derived later instead of being a one-off judgement.

    (The counter entries -- counter-tsu, Number + つ -- are listed for the same
    reason but derive no spec at all; they were already skipped.)
    """
    for rid in ("jikan-time-duration", "issho-ni-together", "ichiban-superlative"):
        rule = next(r for r in _DATA_RULES if r["key"] == "ds_" + rid)
        assert rule["skipped"] is True
        assert rule["patterns"] == [], "a skipped rule must carry no matcher"
        assert rule["derived"], rid


def test_vocab_ids_are_library_entries():
    """
    The reviewed list is frozen on purpose: a typo here would quietly screen
    nothing, and adding an entry means someone read its panel row and decided
    it is vocabulary.  Both are worth a test failure.
    """
    from scripts.screen_grammar_library import load_library

    library = load_library()
    assert _VOCAB_IDS <= set(library), sorted(_VOCAB_IDS - set(library))
    assert len(_VOCAB_IDS) == 14, "changing the list is a review, not a patch"


def test_vocabulary_screen_still_flags_the_vocab_ids():
    """
    The screen and the list must not drift apart.  If the library is
    re-vendored or the derivation changes, the entries the list removes have
    to still look like vocabulary to the screen -- otherwise the list is stale
    and should be re-derived, not trusted.
    """
    from scripts.screen_grammar_library import screen

    flagged = {rid for rid, _rule, _entry, _lits, _heads in screen()}
    live = {
        r["key"][3:]
        for r in _DATA_RULES
        if r["skipped"] and r["key"][3:] in _VOCAB_IDS and r["derived"]
    }
    assert live, "expected the reviewed vocabulary entries to derive a spec"
    assert live <= flagged, sorted(live - flagged)


# --- patterns that name a form instead of a literal (see _SLOT_SPECS) ------
#
# "Verb ない-form + で" names a form, not a string.  The pattern yields no
# literal, so the derivation fell back to the entry's formation field -- a list
# of *examples* of that form -- and titled the row after whichever example came
# first: "〜言わないで", a row that then never appeared at all.  The other such
# entry, "い-adjective + (です)", is not a construction the sentence is made of
# and gets no row at all -- see _CONCEPT_IDS.


def test_slot_specs_match_their_own_examples():
    """
    The load-time guard, asserted so that it never actually fires: a reviewed
    spec that no longer fits its entry's examples is dropped and the entry
    falls back to the old derivation.  This is what would catch that.
    """
    from lute.read.render.grammar_analysis_ja import _spec_matches, _tokens_for
    from scripts.screen_grammar_library import load_library

    library = load_library()
    assert _SLOT_SPECS, "expected the reviewed slot specs to be non-empty"
    for rid, (label, specs) in _SLOT_SPECS.items():
        assert rid in library, rid
        assert label, rid
        entry = library[rid]
        joined = "".join(e["japanese"] for e in entry["examples"])
        tokens = _tokens_for(joined)
        for spec in specs:
            assert _spec_matches(spec, tokens, joined), rid


def test_i_adjective_nonpast_gets_no_row():
    """
    Whether an い-adjective is in its dictionary form is a property of the
    word, not a construction the sentence is made of: the panel reports the
    latter.  Matched on 形容詞 in dictionary form the entry hit 83% of a
    400-page corpus -- the dictionary form is the default form, so nearly
    every page carried the row -- and it said nothing the reader could not see
    in the word itself.  The forms that do have to be recognised keep their
    own rows, which is what this checks on both sides.
    """
    for sentence in ("この本は高いです。", "その映画は面白い。", "新しい本を読んだ。"):
        assert "ds_i-adjective-nonpast" not in _keys(sentence), sentence
    assert "ds_i-adjective-past" in _keys("昨日は寒かったです。")
    assert "ds_i-adjective-negative" in _keys("高くありません。")


def test_nai_de_reports_without_doing():
    """
    "Verb ない-form + で" is 動詞 + ない + で -- Sudachi reports that ない as
    助動詞, so no part-of-speech lookup finds it and the entry has to say so
    itself.  Its old row was titled 〜言わないで, one of its own examples.
    """
    for sentence in (
        "朝ごはんを食べないで学校へ行った。",
        "何も言わないでください。",
        "勉強しないで遊んでいた。",
    ):
        assert "ds_nai-de-without-doing" in _keys(sentence), sentence
    for sentence in ("これは本ではない。", "知らないです。", "少なくないです。"):
        assert "ds_nai-de-without-doing" not in _keys(sentence), sentence


def test_te_kudasai_covers_the_de_form():
    """
    〜てください is one request however the verb's て-form is spelled -- 読んで /
    遊んで / 死んで are the same form.  The hand-written rule matched a bare て,
    so 読んでください was not reported as 〜てください at all; the data entry for
    〜ないでください, whose ない had been dropped from its spec, claimed it
    instead and called it "please don't".
    """
    for sentence in ("ここに名前を書いてください。", "読んでください。", "遊んでください。"):
        assert "te_kudasai" in _keys(sentence), sentence
    # The hand-written rule is the only 〜てください row; the data entry it
    # covers stays suppressed even though the hand-written spec is a token
    # spec now, not the regex the suppression used to be derived from.
    assert "ds_te-kudasai-request" not in _keys("読んでください。")


def test_nai_de_kudasai_needs_its_nai():
    """
    〜ないでください is "please don't", and the ない is the whole difference:
    the spec was the bare literal でください, which matched every でください in
    the language -- including the で-form of a plain request.
    """
    for sentence in ("何も言わないでください。", "心配しないでください。"):
        assert "ds_nai-de-kudasai" in _keys(sentence), sentence
    assert any(
        e["name"] == "〜ないでください"
        for e in analyze_japanese("心配しないでください。", display_lang="zh")
    )
    for sentence in ("ここに名前を書いてください。", "読んでください。", "これは本ではない。"):
        assert "ds_nai-de-kudasai" not in _keys(sentence), sentence


def test_concept_entries_get_no_row():
    """
    jidoushi-tadoushi is a category article.  A sentence does not contain "the
    transitive / intransitive distinction"; the verb in it does, and the word
    popup already says which one that is.  Its row was titled after two
    arbitrary members of its example list (〜開く・消す) and appeared on 11.2%
    of pages.  The constructions those verbs are in still report.
    """
    for sentence in ("ドアが開いている。", "電気を消してください。"):
        assert "ds_jidoushi-tadoushi" not in _keys(sentence), sentence
    assert "te_iru" in _keys("ドアが開いている。")
    assert "te_kudasai" in _keys("電気を消してください。")


def test_reviewed_silent_ids_are_library_entries():
    "Same guard as _VOCAB_IDS: a typo would silently stop screening anything."
    from scripts.screen_grammar_library import load_library

    library = load_library()
    assert _CONCEPT_IDS <= set(library), sorted(_CONCEPT_IDS - set(library))
    assert len(_CONCEPT_IDS) == 2, "changing the list is a review, not a patch"
    for rid in _CONCEPT_IDS:
        rule = next(r for r in _DATA_RULES if r["key"] == "ds_" + rid)
        assert rule["skipped"] is True
        assert rule["patterns"] == [], "a skipped rule must carry no matcher"
        assert rule["derived"], rid


# Rules whose headline comes from the formation field yet is still the
# construction itself -- the pattern's literals sit inside parenthetical
# alternatives ("Noun + 向け(に / の)"), which the derivation cannot read.
_FORMATION_NAMED = {
    "muke-targeted-for",
    "muki-suitable-for",
    "hoka-nai-no-choice",
    "wo-hajime-including",
}


def test_rules_are_named_after_their_own_pattern():
    """
    A row's headline has to come from the entry it reports.  When the pattern
    names no literal, the derivation falls back to the formation field, which
    for an entry describing a form is a list of examples -- and the row is then
    named after an example word.  _SLOT_SPECS and _CONCEPT_IDS fix the three
    that did; this keeps the class from growing back unnoticed.
    """
    from scripts.screen_grammar_library import load_library

    library = load_library()
    named_from_examples = set()
    for rule in _DATA_RULES:
        rid = rule["key"][3:]
        if rule["skipped"] or rid in _SLOT_SPECS:
            continue
        pattern = library[rid]["pattern"]
        if any(
            frag and frag not in pattern
            for frag in rule["pattern"].lstrip("〜").split("・")
        ):
            named_from_examples.add(rid)
    assert named_from_examples == _FORMATION_NAMED, sorted(
        named_from_examples - _FORMATION_NAMED
    )


# --- left-context exclusions (see _NOT_AFTER) -------------------------------
#
# "Verb + ます" cannot tell 知っ**て**い**ます** from 行き**ます**: the auxiliary
# い is 動詞,非自立可能, and so are 行き / 来 / あり, so no part of speech
# separates them.  What precedes the run does.


@pytest.mark.parametrize(
    "sentence",
    [
        "店長は知っています。",
        "彼は本を読んでいます。",  # で form of the te-form
        "子供が遊んでいます。",
        "もう食べてしまいました。",
        "雨が降っていました。",
        "書いておきました。",
    ],
)
def test_te_chain_is_not_read_as_the_polite_suffix(sentence):
    """
    A 〜て + auxiliary chain is a grammar point of its own (〜ている, 〜てしまう,
    〜ておく ...) and has its own row.  The polite-suffix row must not claim it:
    it used to offer 知っています as an example of 〜ます, with います highlighted.
    """
    entries = analyze_japanese(sentence, display_lang="zh")
    forms = [e for e in entries if e["key"] == "basic_forms"]
    assert forms == [], [e["name"] for e in entries]


@pytest.mark.parametrize(
    "sentence",
    [
        "毎朝、コーヒーを飲みます。",
        "昨日、映画を見ました。",
    ],
)
def test_polite_suffix_still_reports(sentence):
    "The ordinary polite form is still a basic form."
    assert "basic_forms" in _keys(sentence), sentence


def test_polite_suffix_is_not_blocked_by_the_homograph_de():
    """
    The で of 電車**で**行きます ("go by train") is 助詞,格助詞 -- a bare て/で
    exclusion would drop this sentence, which is an ordinary 〜ます.  Only the
    te-form connective (助詞,接続助詞) may block.
    """
    for sentence in ("電車で行きます。", "東京で会いました。", "鉛筆で書きました。"):
        assert "basic_forms" in _keys(sentence), sentence


def test_not_after_is_wired_into_the_specs():
    "Guards the reviewed list against a typo quietly excluding nothing."
    from lute.read.render.grammar_analysis_ja import _NOT_AFTER, _TE_CONNECTIVE

    from scripts.screen_grammar_library import load_library

    library = load_library()
    assert set(_NOT_AFTER) <= set(library), sorted(set(_NOT_AFTER) - set(library))
    for rid, cond in _NOT_AFTER.items():
        rule = next(r for r in _DATA_RULES if r["key"] == "ds_" + rid)
        token_specs = [s for s in rule["patterns"] if s["type"] == "tokens"]
        assert token_specs, rid
        assert all(s.get("not_after") == cond for s in token_specs), rid
    assert _TE_CONNECTIVE["pos2"] == "接続助詞"


def test_korean_display_language_switches_desc():
    "한국어 display uses ko.json for data rules and _KO_HAND for hand-written."
    ko = {e["name"]: e for e in analyze_japanese("日本に行きたいです。", "ko")}
    en = {e["name"]: e for e in analyze_japanese("日本に行きたいです。", "en")}
    assert "소망" in ko["〜たい"]["desc"]
    assert "want to do" not in ko["〜たい"]["desc"]
    assert "want to do" in en["〜たい"]["desc"]


def test_korean_display_covers_data_rules():
    "A data-driven rule shows its ko.json gloss."
    ko = {e["name"]: e for e in analyze_japanese("明日は雨でしょう。", "ko")}
    assert "추측" in ko["〜でしょう"]["desc"]


def test_korean_display_translates_aggregates():
    "The two aggregate buckets show Korean descriptions too."
    res = analyze_japanese("私は学生です。あれは本です。", "ko")
    basics = next(g for g in res if g["key"] == "basic_forms")
    assert "기초" in basics["desc"]
