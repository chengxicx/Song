"""
gloss_format.parse_gloss: structuring the flattened dictionary gloss.

The expectations below are pinned to real entries served by the
production pool (MOJi辞書, sampled 2026-10-12 via the Local tab), so a
rule change that would garble the actual dictionaries shows up here.
"""

from lute.dict.gloss_format import parse_gloss


def types(blocks):
    return [b["t"] for b in blocks]


def test_a_verb_entry_gets_pos_senses_and_example_pairs():
    "食べる, as the dictionary serves it."
    gloss = "\n".join(
        [
            "他动·二类惯用语",
            "他动·二类",
            "吃。（飲食物をいただく。）",
            "ご飯を食べる。",
            "吃饭。",
            "腹いっぱい食べた。",
            "吃饱了。",
            "生活。（生計を立てる。）",
            "月給で食べる。",
            "靠工资维持生活。",
            "惯用语",
            "食べてすぐ寝ると牛になる。／吃完饭马上躺下会变成牛。",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == [
        "pos",
        "sense",
        "example",
        "example",
        "sense",
        "example",
        "section",
        "example",
    ]
    assert blocks[0]["text"] == "他动·二类"  # the mashed section word comes off
    assert blocks[1]["n"] == 1
    assert blocks[1]["text"] == "吃。（飲食物をいただく。）"
    assert blocks[2]["text"] == "ご飯を食べる。"
    assert blocks[2]["translation"] == "吃饭。"
    assert blocks[4]["n"] == 2  # the second sense keeps counting
    assert blocks[5]["translation"] == "靠工资维持生活。"
    assert blocks[6]["text"] == "惯用语"
    assert "translation" not in blocks[7]  # the trailing 惯用语 line stands alone


def test_a_kana_headword_line_is_kept_as_an_annotation():
    "すみません: the entry opens with its own kanji headword and accent."
    gloss = "\n".join(
        [
            "済みません【すみません④】",
            "接续",
            "对不起抱歉劳驾谢谢(寒暄的客气语)",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["headword", "pos", "sense"]
    assert blocks[0]["text"] == "済みません【すみません④】"
    assert blocks[1]["text"] == "接续"
    assert blocks[2]["n"] == 1


def test_see_also_lines_become_cross_references():
    "ありがとう: a homophone disambiguation entry."
    gloss = "See also: 有り難う【ありがとう②】\nSee also: 有難う【ありがとう②】"
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["xref", "xref"]
    assert blocks[0]["text"] == "有り難う"
    assert blocks[0]["reading"] == "ありがとう②"


def test_a_kanji_only_example_line_is_paired_with_its_translation():
    "電車: 一番電車 has no kana, but the next line is clearly its translation."
    gloss = "\n".join(
        [
            "名补充说明",
            "名",
            "电车。（電力でレールの上を走り、客や貨物を運ぶ車両。）",
            "一番電車",
            "首班车；头班车；头班电车。",
            "電車賃",
            "电车费。",
            "补充说明",
            "汉语的“电车”泛指以电力为能源的交通工具，坊间所说的“电车”一般指无轨电车或有轨电车。而火车统称“火车”。",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == [
        "pos",
        "sense",
        "example",
        "example",
        "section",
        "note",
    ]
    assert blocks[0]["text"] == "名"  # 「名补充说明」 -- POS with a mashed section word
    assert blocks[2]["text"] == "一番電車"
    assert blocks[2]["translation"] == "首班车；头班车；头班电车。"
    assert blocks[3]["text"] == "電車賃"
    assert blocks[4]["text"] == "补充说明"
    assert blocks[5]["t"] == "note"  # the long paragraph is prose, not a numbered sense


def test_sense_numbering_restarts_after_a_pos_badge():
    gloss = "\n".join(
        [
            "自动·一类",
            "坚持。（あくまでも主張する。）",
            "自动·二类",
            "起床。（ねどこをはなれる。）",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["pos", "sense", "pos", "sense"]
    assert blocks[1]["n"] == 1
    assert blocks[3]["n"] == 1


def test_a_consecutive_duplicate_pos_line_is_dropped():
    gloss = "名\n名\n图书馆。（施設。）"
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["pos", "sense"]
    assert sum(1 for b in blocks if b["t"] == "pos") == 1


def test_kana_inside_parentheses_is_a_sense_not_an_example():
    "教師，教员，老师；师傅。（学問、技芸などを教える人。）"
    gloss = "名\n教师，教员，老师；师傅。（学問、技芸などを教える人。）"
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["pos", "sense"]
    assert blocks[1]["text"].startswith("教师")


def test_no_gloss_yields_no_blocks():
    assert parse_gloss(None) == []
    assert parse_gloss("") == []
    assert parse_gloss("\n \n") == []


def test_unusual_text_degrades_to_senses_without_losing_any():
    "A dictionary with different conventions must not lose lines."
    gloss = "a pleasant feeling\ngiving pleasure"
    blocks = parse_gloss(gloss)

    assert [b["text"] for b in blocks] == ["a pleasant feeling", "giving pleasure"]
    assert all(b["t"] == "sense" for b in blocks)
    assert [b["n"] for b in blocks] == [1, 2]
