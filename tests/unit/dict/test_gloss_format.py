"""
gloss_format.parse_gloss: structuring the flattened dictionary gloss.

The expectations below are pinned to real entries served by the
production pool (MOJi辞書, sampled 2026-10-12 via the Local tab), so a
rule change that would garble the actual dictionaries shows up here.
"""

from lute.dict.gloss_format import COMPACT_GLOSS_MAX_CHARS, compact_gloss, parse_gloss


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


# --- English (Latin-script) dictionary entries ------------------------------
#
# The pool's Japanese rules key off kana; an English MDX entry used to
# fall through all of them and render as an undifferentiated numbered
# list -- phonetics, POS, examples and senses alike.  These pin the
# English conventions on entries laid out the way Oxford/Learner's
# style dictionaries flatten.


def test_an_english_bilingual_entry_gets_phonetics_pos_senses_and_examples():
    "word, as an Oxford-style 英汉双解 entry flattens."
    gloss = "\n".join(
        [
            "/wɜːd/ ; NAmE /wɜːrd/",
            "noun",
            "1. 词；单词；字",
            "a unit of language",
            "2. 诺言；保证",
            "a promise",
            "He is a man of his word.",
            "他是个说话算数的人。",
            "See also: wordy",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == [
        "phonetic",
        "pos",
        "sense",
        "sense",
        "example",
        "xref",
    ]
    assert blocks[0]["text"] == "/wɜːd/ ; NAmE /wɜːrd/"
    assert blocks[1]["text"] == "noun"
    assert blocks[2]["n"] == 1  # the source number is kept, not renumbered
    assert blocks[2]["en"] == "a unit of language"  # the bilingual counterpart
    assert blocks[3]["n"] == 2
    assert blocks[3]["en"] == "a promise"
    assert blocks[4]["text"] == "He is a man of his word."
    assert blocks[4]["translation"] == "他是个说话算数的人。"
    assert blocks[5] == {"t": "xref", "text": "wordy", "reading": None}


def test_english_pos_lines_and_pos_prefixed_senses():
    gloss = "\n".join(
        [
            "vt., vi.",
            "n. 词；话语",
            "adj（only before noun）",
            "adj. 只作定语",
            "verb [T]",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["pos", "pos", "sense", "pos", "pos", "sense", "pos"]
    assert blocks[0]["text"] == "vt., vi."
    assert blocks[1]["text"] == "n."  # the prefix splits off as its badge
    assert blocks[2]["text"] == "词；话语"
    assert blocks[2]["n"] == 1
    assert blocks[3]["text"] == "adj（only before noun）"  # label and all
    assert blocks[4]["text"] == "adj."
    assert blocks[5]["text"] == "只作定语"
    assert blocks[6]["text"] == "verb [T]"  # Longman-style label


def test_a_bracketed_phonetic_line_is_phonetics_too():
    blocks = parse_gloss("[wɜːd]")
    assert types(blocks) == ["phonetic"]

    # Text around the groups that is not a variety label is a definition.
    blocks = parse_gloss("either / or")
    assert types(blocks) == ["sense"]


def test_numbers_starting_a_definition_are_not_sense_numbers():
    "1.5 million: the digit after the dot keeps it a plain definition."
    blocks = parse_gloss("1.5 million")

    assert types(blocks) == ["sense"]
    assert blocks[0]["text"] == "1.5 million"


def test_numbered_english_examples_pair_with_their_translations():
    gloss = "\n".join(["1. He kept his word.", "他遵守了诺言。"])
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["example"]
    assert blocks[0]["text"] == "He kept his word."  # the marker comes off
    assert blocks[0]["translation"] == "他遵守了诺言。"


def test_an_english_counterpart_attaches_only_to_a_chinese_sense():
    "英英 entries: English lines stay their own numbered senses."
    gloss = "\n".join(
        [
            "1. to move something",
            "2. to capture something",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["sense", "sense"]
    assert "en" not in blocks[0]
    assert [b["n"] for b in blocks] == [1, 2]


# --- compact_gloss: the entry as a term's translation -----------------------
#
# The term form's auto-fill saves this (see /read/localdict_lookup): a
# translation holding the whole card -- examples and all -- makes every
# popup and flashcard that shows it enormous.


def test_compact_keeps_the_pos_and_senses_and_drops_the_examples():
    "食べる again: the card's definitions, without its example tail."
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

    assert compact_gloss(gloss) == (
        "他动·二类\n吃。（飲食物をいただく。）\n生活。（生計を立てる。）"
    )


def test_compact_caps_the_total_length_with_an_ellipsis():
    line = "第{}条长长的释义。（这一段说明文字没有任何含义，它在这里只是为了把每一条的长度撑到足以越过预算的程度。）"
    gloss = "\n".join(line.format(c) for c in "一二三四五六")
    line_len = len(line.format("一"))
    assert (
        2 * line_len < COMPACT_GLOSS_MAX_CHARS < 6 * line_len
    ), "the fixture must straddle the budget for the test to mean anything"

    compact = compact_gloss(gloss)

    lines = compact.split("\n")
    assert lines[-1] == "…", "the cut is visible"
    assert "第六条" not in compact, "the senses past the budget are gone"
    assert sum(len(ln) for ln in lines[:-1]) <= COMPACT_GLOSS_MAX_CHARS


def test_compact_keeps_a_see_also_pointer():
    gloss = "See also: 見開き【みひらき】"

    assert compact_gloss(gloss) == "See also: 見開き【みひらき】"


def test_compact_of_an_english_entry_keeps_the_chinese_definitions():
    "word again: no phonetics, no English counterparts, no examples."
    gloss = "\n".join(
        [
            "/wɜːd/ ; NAmE /wɜːrd/",
            "noun",
            "1. 词；单词；字",
            "a unit of language",
            "2. 诺言；保证",
            "a promise",
            "He is a man of his word.",
            "他是个说话算数的人。",
            "See also: wordy",
        ]
    )

    assert compact_gloss(gloss) == (
        "noun\n词；单词；字\n诺言；保证\nSee also: wordy"
    )


def test_compact_keeps_an_english_see_also_without_reading():
    assert compact_gloss("See also: wordy") == "See also: wordy"


def test_compact_falls_back_to_the_raw_lines_when_nothing_parses():
    "A wall of examples still yields a definition rather than nothing."
    gloss = "\n".join(["一番電車", "头班电车。", "執務時間", "办公时间。"])

    assert compact_gloss(gloss) == gloss


def test_compact_passes_an_empty_gloss_through():
    assert compact_gloss(None) is None
    assert compact_gloss("") == ""
