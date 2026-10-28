"""
gloss_format.parse_gloss: structuring the flattened dictionary gloss.

The expectations below are pinned to real entries served by the
production pool (MOJi辞書, sampled 2026-10-12 via the Local tab), so a
rule change that would garble the actual dictionaries shows up here.
"""

from lute.dict.gloss_format import (
    COMPACT_GLOSS_MAX_CHARS,
    compact_gloss,
    parse_gloss,
    popup_gloss,
)


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

    assert compact_gloss(gloss) == ("noun\n词；单词；字\n诺言；保证\nSee also: wordy")


def test_compact_keeps_an_english_see_also_without_reading():
    assert compact_gloss("See also: wordy") == "See also: wordy"


def test_compact_falls_back_to_the_raw_lines_when_nothing_parses():
    "A wall of examples still yields a definition rather than nothing."
    gloss = "\n".join(["一番電車", "头班电车。", "執務時間", "办公时间。"])

    assert compact_gloss(gloss) == gloss


def test_compact_passes_an_empty_gloss_through():
    assert compact_gloss(None) is None
    assert compact_gloss("") == ""


# ---------------------------------------------------------------------------
# Marked glosses: the entries that arrive as ONE line, with the source
# markup's icons left inline (the Oxford conversion in the pool).
# ---------------------------------------------------------------------------

# 牛津高阶双解, sampled 2026-10-27 through the Local tab.
HAPPY = (
    "🔑 happyBrE /ˈhæpi/NAmE /ˈhæpi/ adjective (hap·pier, hap·pi·est) happy adjective"
    "🔑 (≠ unhappy) happily adverb"
    "➤feeling/giving pleasure 感到／给予快乐"
    "🔑 feeling or showing pleasure; pleased 感到（或显得）快乐的；高兴的"
    "◆a happy smile/face快活的微笑／面容"
    "◆You don't look very happy today.  你今天好像不太高兴。🔊🔊"
    "gladhappy ◇ pleased ◇ delighted ◇ proud"
    "These words all describe people feeling happy about sth. 以上各词均形容人感到高兴。"
)

THE = (
    "🔑 theBrE /ðə/NAmE /ðə/ definite article"
    "🔑 used to refer to sb/sth that has already been mentioned"
    "（指已提到或易领会到的人或事物）"
    "◆the Mona Lisa《蒙娜丽莎》"
    "🔑 used to refer to sb/sth that is the only, normal or obvious one of their kind"
    "（指独一无二的、正常的或不言而喻的人或事物）"
)

GIVE = (
    "🔑 giveBrE /ɡɪv/NAmE /ɡɪv/ verbpresent simple - I / you / we / they give"
    " BrE /ɡɪv/ NAmE /ɡɪv/past simple gave"
    "➤hand/provide 交给；提供"
    "🔑  [T] to hand sth to sb so that they can look at it 给；交给"
    "◆~ sth to sb Give the letter to your mother when you've read it."
    "  信看完后交给你母亲。"
)

A_ARTICLE = (
    "🔑 aBrE /ə/NAmE /ə/ indefinite article"
    " HELP The form  a  is used before consonant sounds and the form  an  before vowel"
    " sounds. When saying abbreviations like 'FM' or 'UN', use  a  or  an."
    "◆an FM radio U  is a vowel but begins with /j/ and so you say:"
    "🔑 used before countable or singular nouns referring to people or things"
    " （用于可数名词或单数名词前，表示未曾提到的）一（人、事、物）"
)


def test_a_marked_entry_becomes_a_headword_guide_word_senses_and_examples():
    "The 800-character wall, split on the markers its markup left behind."
    blocks = parse_gloss(HAPPY)

    assert types(blocks) == [
        "headword",
        "note",
        "note",
        "section",
        "sense",
        "example",
        "example",
    ], "word-family lines are notes, ➤ is a header, ◆ are examples"
    assert blocks[0]["text"] == "BrE /ˈhæpi/NAmE /ˈhæpi/ adjective"
    assert blocks[3]["text"] == "feeling/giving pleasure 感到／给予快乐"
    assert blocks[4] == {
        "t": "sense",
        "text": "感到（或显得）快乐的；高兴的",
        "en": "feeling or showing pleasure; pleased",
        "n": 1,
    }
    assert blocks[5]["text"] == "a happy smile/face"
    assert blocks[5]["translation"] == "快活的微笑／面容"
    assert blocks[6]["translation"] == "你今天好像不太高兴。"


def test_the_markers_themselves_never_reach_the_rendered_text():
    for gloss in (HAPPY, THE, GIVE, A_ARTICLE):
        for block in parse_gloss(gloss):
            shown = " ".join(
                str(block.get(key) or "") for key in ("text", "en", "translation")
            )
            for mark in ("🔑", "➤", "◆", "◇", "🔊"):
                assert mark not in shown, f"{mark} leaked into {block}"


def test_a_sense_whose_translation_opens_with_a_wide_bracket_keeps_it():
    "the first sense: （指已提到…） is the translation, not the definition."
    blocks = parse_gloss(THE)
    senses = [b for b in blocks if b["t"] == "sense"]

    assert senses[0]["text"] == "（指已提到或易领会到的人或事物）"
    assert senses[0]["en"] == (
        "used to refer to sb/sth that has already been mentioned"
    )
    assert senses[0]["n"] == 1
    assert senses[1]["n"] == 2, "numbering continues across the entry"


def test_a_synonym_study_is_dropped_rather_than_shown_as_an_example():
    "◇ starts prose about the synonym group, not this entry's meaning."
    shown = " ".join(str(b.get("text") or "") for b in parse_gloss(HAPPY))

    assert "pleased" not in shown
    assert "以上各词" not in shown
    assert "gladhappy" not in shown, "its first synonym is glued to the previous text"


def test_a_help_usage_note_is_dropped_along_with_its_examples():
    "HELP runs until the next sense: it is guidance, not the meaning."
    blocks = parse_gloss(A_ARTICLE)

    shown = " ".join(str(b.get("text") or "") for b in blocks)
    assert "The form" not in shown
    assert "FM radio" not in shown
    assert [b["t"] for b in blocks] == ["headword", "sense"]
    assert blocks[1]["text"].startswith("（用于可数名词或单数名词前")


def test_a_short_key_without_a_translation_is_a_word_family_line_not_a_sense():
    "happy's '🔑 (≠ unhappy) happily adverb' is a cross-reference."
    blocks = parse_gloss(HAPPY)

    assert blocks[2] == {"t": "note", "text": "(≠ unhappy) happily adverb"}


def test_a_conjugation_table_glued_onto_the_pos_label_is_cut_off():
    "give: 'verbpresent simple - I ...' -- only the label survives."
    blocks = parse_gloss(GIVE)

    assert blocks[0]["t"] == "headword"
    assert blocks[0]["text"] == "BrE /ɡɪv/NAmE /ɡɪv/ verb"
    assert "present simple" not in blocks[0]["text"]


def test_a_marked_entry_still_compacts_to_its_chinese_definitions():
    "The term form's auto-fill wants the meaning, not the examples."
    assert compact_gloss(HAPPY) == "感到（或显得）快乐的；高兴的"


def test_a_related_words_list_glued_onto_the_last_translation_is_dropped():
    "book: the word-use study's list lands in the last example's translation."
    gloss = (
        "🔑 bookBrE /bʊk/NAmE /bʊk/ noun"
        "🔑  [C] a set of printed pages 书；书籍"
        "◆reference/children's/library books 参考书；儿童读物；馆藏书籍"
        " author,  book,  classic,  critic,  drama"
    )
    blocks = parse_gloss(gloss)

    assert blocks[-1]["t"] == "example"
    assert blocks[-1]["translation"] == "参考书；儿童读物；馆藏书籍"


# subconscious, as the Local tab served it: the entry has no 🔑 at all --
# only its ◆ examples are marked -- so the headword, the phonetics, the
# POS and the first definition arrived as one run.
SUBCONSCIOUS = (
    "1 sub·con·sciousBrE /ˌsʌbˈkɒnʃəs/NAmE /ˌsʌbˈkɑːnʃəs/ adjective"
    " [usually before noun] connected with a part of your mind that stores"
    " memories and makes you behave in a particular way 潜意识的；下意识的"
    "◆a subconscious fear 下意识的恐惧"
    "◆the subconscious mind 潜意识"
    "➡ compare conscious ▸ sub·con·scious·lyBrE /ˌsʌbˈkɒnʃəsli/ adverb 下意识地"
)


def test_an_entry_with_no_key_marks_still_gets_its_headword_and_first_sense():
    "subconscious: only ◆ is marked; the opening run used to be one sense."
    blocks = parse_gloss(SUBCONSCIOUS)

    assert types(blocks) == ["headword", "sense", "example", "example", "note", "note"]
    assert blocks[0]["text"] == "BrE /ˌsʌbˈkɒnʃəs/NAmE /ˌsʌbˈkɑːnʃəs/ adjective"
    assert blocks[1]["n"] == 1, "the definition is the entry's first sense"
    assert blocks[1]["text"] == "潜意识的；下意识的"
    assert blocks[1]["en"].startswith("[usually before noun] connected with")
    assert blocks[2]["text"] == "a subconscious fear"
    assert blocks[2]["translation"] == "下意识的恐惧"
    assert blocks[4]["text"] == "compare conscious", "➡ is a note, not a sense tail"
    assert "sub·con·scious·ly adverb" in blocks[5]["text"]
    assert "/ˌsʌbˈkɒnʃəsli/" not in blocks[5]["text"], "the note drops the phonetics"


def test_an_unmarked_entry_is_unchanged_by_the_headword_split():
    "No markers at all: the rules below read the lines as they always did."
    gloss = "a pleasant feeling\ngiving pleasure"
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["sense", "sense"]
    assert [b["text"] for b in blocks] == ["a pleasant feeling", "giving pleasure"]


# echoes, as the Local tab served it: same as subconscious, with the
# extra damage that senses 2 and 3 lost their marker entirely and were
# glued onto the translations of the examples before them.
ECHOES = (
    "echoBrE /ˈekəʊ/NAmE /ˈekoʊ/ noun (plural echoes) the reflecting of"
    " sound off a wall or inside a confined space so that a noise appears"
    " to be repeated; a sound that is reflected back in this way 回响； 回声； 回音"
    "◆There was an echo on the phone and I couldn't hear clearly."
    " 电话里有回音，我听不清楚。 🔊🔊"
    "◆The hills sent back a faint echo. 座座山丘传来微弱的回声。 🔊🔊"
    "◆the echo of footsteps running down the corridor沿走廊跑的脚步声回声"
    " the fact of an idea, event, etc. being like another and reminding"
    " you of it; sth that reminds you of sth else 映现； 暗示； 启示； 反响"
    "◆Yesterday's crash has grim echoes of previous disasters."
    " 昨天的撞车事故使人想起之前那些令人痛心的灾难。 🔊🔊"
    "an opinion or attitude that agrees with or repeats one already"
    " expressed or thought 共鸣； 附和； 重复"
    "◆His words were an echo of what she had heard many times before."
    " 他的话她以往听过许多次。 🔊🔊"
    "◆The speech found an echo"
)


def test_a_definition_glued_onto_an_examples_translation_becomes_a_sense():
    "echoes: the English after a Chinese gloss is the next definition."
    blocks = parse_gloss(ECHOES)

    assert types(blocks) == [
        "headword",
        "sense",
        "example",
        "example",
        "example",
        "sense",
        "example",
        "sense",
        "example",
        "example",
    ]
    senses = [b for b in blocks if b["t"] == "sense"]
    assert [b["text"] for b in senses] == [
        "回响； 回声； 回音",
        "映现； 暗示； 启示； 反响",
        "共鸣； 附和； 重复",
    ]
    assert [b["n"] for b in senses] == [1, 2, 3]
    # The cut lands between the example and the definition glued after
    # its translation, so neither carries the other's text.
    assert blocks[4]["translation"] == "沿走廊跑的脚步声回声"
    assert blocks[5]["en"].startswith("the fact of an idea")


# --- Oxford's inline apparatus --------------------------------------------
#
# Beyond the markers, the Oxford conversion glues three more kinds of
# source material onto entries: the "~" sense patterns, the OPP
# antonym pointer, and the topic/appendix lists.  Each used to land
# inside a translation or replace the entry's own sense.

ABLAZE = (
    "ablazeBrE /əˈbleɪz/NAmE /əˈbleɪz/ adjective [not before noun]"
    " burning quickly and strongly 猛烈燃烧"
    "◆The whole building was soon ablaze.  整栋大楼很快就熊熊燃烧起来。🔊🔊"
    "◆Cars and buses were  set ablaze  during the riot."
    "  暴乱中许多轿车和公共汽车被纵火焚烧。🔊🔊"
    "full of bright light or colours 闪耀；发光；明亮；色彩鲜艳"
    "◆There were lights still ablaze as they drove up to the house."
    "  他们驾车到屋前时，（屋内）灯火仍然通明。🔊🔊"
    "◆~ with sth The trees were ablaze with the colours of autumn."
    "  树木披上了绚丽的秋装。🔊🔊"
    "~ (with sth) full of strong emotion or excitement 充满激情的；情绪激动的"
    "◆He turned to her, his eyes ablaze with anger."
    "  他转过身来，怒目圆睁瞪着她。🔊🔊"
)


def test_a_tilde_sense_pattern_after_a_translation_opens_a_new_sense():
    "ablaze: the last sense's '~ (with sth)' was glued to an example."
    blocks = parse_gloss(ABLAZE)

    assert types(blocks) == [
        "headword",
        "sense",
        "example",
        "example",
        "sense",
        "example",
        "example",
        "sense",
        "example",
    ]
    assert blocks[6]["translation"] == "树木披上了绚丽的秋装。"
    assert blocks[7] == {
        "t": "sense",
        "text": "充满激情的；情绪激动的",
        "en": "~ (with sth) full of strong emotion or excitement",
        "n": 3,
    }


ABILITY = (
    "🔑 abil·ityBrE /əˈbɪləti/NAmE /əˈbɪləti/ noun (plural abil·ities)"
    " 🔑  [sing.] ~ to do sth the fact that sb/sth is able to do sth 能力"
    "◆The system has the ability to run more than one program at the same time."
    "  该系统能够同时运行一个以上的程序。🔊🔊"
    "◆A gentle form of exercise will increase your ability to relax."
    "  和缓的运动锻炼会提高自我放松的能力。🔊🔊 OPP inability"
    " 🔑  [C, U] a level of skill or intelligence 才能；本领；才智"
    "◆Almost everyone has some musical ability.  几乎人人都有一些音乐才能。🔊🔊"
)


def test_an_opposite_pointer_is_a_note_not_part_of_the_translation():
    "ability: 'OPP inability' rides on the last example of a sense."
    blocks = parse_gloss(ABILITY)

    assert {"t": "note", "text": "OPP inability"} in blocks
    assert blocks[3]["translation"] == "和缓的运动锻炼会提高自我放松的能力。"
    assert all("OPP" not in b.get("translation", "") for b in blocks)


def test_a_source_vocabulary_list_glued_to_a_translation_is_cut():
    "airing cupboard: an appendix list follows the meaning, not the meaning."
    gloss = (
        "airing cupboardBrE /ˈeərɪŋ kʌbəd/NAmE /ˈerɪŋ kʌbərd/ noun (BrE)"
        " a warm cupboard in which clean sheets, clothes, etc. are put to"
        " make sure they are completely dry （烘干衣物的）烘柜"
        "  Compound nouns with –ing forms airing cupboard, answering machine,"
        " asking price, baking powder, betting shop, boarding school,"
        " booking office, bowling alley, breathing space"
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["headword", "sense"]
    assert blocks[1]["text"] == "（烘干衣物的）烘柜"
    assert "Compound nouns" not in blocks[1]["text"]


ACTOR = (
    "🔑 actorBrE /ˈæktə(r)/NAmE /ˈæktər/ noun"
    "a person who performs on the stage, on television or in films/movies,"
    " especially as a profession 演员 actor, ,  cinema,  dialogue,"
    "  director,  dub,  film,  location,  scenario,  sound effect\n"
    "actor,  audition,  body double,  cameo,  cast,  play,  role,  star,"
    "  understudy\n"
    "Masculine, feminine and ungendered form anchorman/\u200banchorwoman,"
    " assemblyman/\u200bassemblywoman, businessman/\u200bbusinesswoman,"
    " actor/\u200bactress, cameraman/\u200bcamerawoman,"
    " chairman/\u200bchairwoman/\u200bchair, congressman/\u200bcongresswoman"
)


def test_a_marked_headword_line_keeps_the_definition_after_its_pos():
    "actor: the first definition shared the run with the headword."
    blocks = parse_gloss(ACTOR)

    assert types(blocks) == ["headword", "sense"]
    assert blocks[0]["text"] == "BrE /ˈæktə(r)/NAmE /ˈæktər/ noun"
    assert blocks[1]["text"] == "演员"
    assert blocks[1]["en"].startswith("a person who performs")


def test_a_trailing_vocabulary_list_line_is_dropped():
    "actor: 'Masculine, feminine and ungendered form …' is an appendix."
    blocks = parse_gloss(ACTOR)

    assert all("Masculine" not in b["text"] for b in blocks)


# --- Collins: one line per sense, no block-level markup -------------------
#
# Collins flattens to "number[POS label] translation；definition" lines
# with the examples underneath.  Without a profile the whole line was
# a "note" and the entry showed no structure at all.


def test_a_collins_entry_gets_pos_senses_and_example_pairs():
    "accordion, as the Collins converter writes it."
    gloss = "\n".join(
        [
            "accordion★☆☆☆☆",
            "1[N-COUNT 可数名词] 手风琴；An accordion is a musical instrument"
            " in the shape of a fairly large box.",
        ]
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["headword", "pos", "sense"]
    assert blocks[0]["text"] == "accordion"  # the frequency stars come off
    assert blocks[1]["text"] == "N-COUNT"
    assert blocks[2] == {
        "t": "sense",
        "text": "手风琴",
        "en": "An accordion is a musical instrument in the shape of a"
        " fairly large box.",
        "n": 1,
    }


COLLINS_HAPPY = "\n".join(
    [
        "happy★★★★☆",
        "1[ADJ-GRADED 能被表示程度的副词或介词词组修饰的形容词]"
        " 高兴的;快乐的；Someone who is happy has feelings of pleasure."
        " [usu ADJ n]",
        "Marina was a confident, happy child...",
        "玛丽娜是个自信、快乐的孩子。",
        "Her face relaxed into a happy smile.",
        "她表情放松下来，露出快乐的微笑。",
        "happilyAlbert leaned back happily and lit a cigarette.",
        "艾伯特高兴地向后倚靠，点了根香烟。",
        "2[ADJ-GRADED 同样的标签] 幸福的；A happy time is full of pleasant"
        " experiences.",
    ]
)


def test_collins_examples_pair_with_their_translations():
    "happy: the English example lines and the Chinese lines under them."
    blocks = parse_gloss(COLLINS_HAPPY)

    examples = [b for b in blocks if b["t"] == "example"]
    assert [b["text"] for b in examples] == [
        "Marina was a confident, happy child...",
        "Her face relaxed into a happy smile.",
        "Albert leaned back happily and lit a cigarette.",
    ]
    assert examples[0]["translation"] == "玛丽娜是个自信、快乐的孩子。"
    assert examples[2]["translation"] == "艾伯特高兴地向后倚靠，点了根香烟。"


def test_a_collins_derived_word_glued_to_its_example_becomes_a_note():
    "happy: 'happilyAlbert leaned back…' is a derived word plus example."
    blocks = parse_gloss(COLLINS_HAPPY)

    assert {"t": "note", "text": "happily"} in blocks
    examples = [b for b in blocks if b["t"] == "example"]
    assert examples[2]["text"].startswith("Albert leaned back happily")


def test_collins_trailing_grammar_labels_stay_out_of_the_definition():
    "happy: the source ends the definition with '[usu ADJ n]'."
    blocks = parse_gloss(COLLINS_HAPPY)

    assert blocks[2]["en"] == "Someone who is happy has feelings of pleasure."
    assert blocks[-1]["n"] == 2  # the source number is kept


def test_english_terms_inside_a_chinese_gloss_do_not_start_a_sense():
    "about: 'about还常用在walk, drive等…' is one Chinese sentence."
    gloss = (
        "除下列用法外，about可用在一些动词、名词和形容词后引出附加信息。"
        "about还常用在walk, drive等表示动作的动词后或mess about，set about"
        " 等短语动词中，这类情况尤见于英国英语。"
    )
    blocks = parse_gloss(gloss)

    assert len(blocks) == 1
    assert blocks[0]["t"] == "note"


# --- popup_gloss: the entry as the term popup shows it ----------------------
#
# The popup is a glance: one line per block, no markers, no headword or
# phonetic chrome (the popup already names the term above it).


def test_the_popup_gloss_lays_a_marked_entry_out_without_its_markers():
    blocks = popup_gloss(SUBCONSCIOUS).split("\n")

    assert blocks[0] == "潜意识的；下意识的"
    assert blocks[1] == "a subconscious fear"
    assert blocks[2] == "下意识的恐惧"
    assert not any(mark in popup_gloss(SUBCONSCIOUS) for mark in ("🔑", "➡", "◆", "▸"))


def test_the_popup_gloss_passes_a_gloss_that_parses_to_nothing_through():
    assert popup_gloss("") == ""
    assert popup_gloss(None) is None


# ---------------------------------------------------------------------------
# Korean (Hangul) dictionary entries: naver 韩汉 and 표준국어대사전
# ---------------------------------------------------------------------------

NAVER_GADA = """가다
[동사]
(1) 去 qù. 走 zǒu. 往 wǎng. 上 shàng. 赴 fù.
도서관에 가다
去图书馆
(2) 上 shàng.
내 아들은 내년에 학교에 간다
我的儿子明年上学"""


def test_naver_korean_entry_gets_pos_numbered_senses_and_example_pairs():
    blocks = parse_gloss(NAVER_GADA)

    assert types(blocks) == [
        "headword",
        "pos",
        "sense",
        "example",
        "sense",
        "example",
    ]
    assert blocks[2]["n"] == 1
    assert blocks[2]["text"] == "去 qù. 走 zǒu. 往 wǎng. 上 shàng. 赴 fù."
    assert blocks[3]["text"] == "도서관에 가다"
    assert blocks[3]["translation"] == "去图书馆"
    assert blocks[4]["n"] == 2
    assert blocks[5]["translation"] == "我的儿子明年上学"


def test_naver_hangul_example_is_not_an_english_counterpart():
    "A Hangul line after a Chinese sense is an example, not the sense's 'en'."
    blocks = parse_gloss(NAVER_GADA)

    assert all("en" not in b for b in blocks)


STANDARD_SARANG = """I
사랑1
[명사]
1 	아끼고 위하는 정성스러운 마음, 또는 그러한 마음을 베푸는 일.
* 어머님의 ~.
*나라와 겨레에 대한 ~.
2 	남녀가 서로 정을 들이어 애틋하게 그리는 마음, 또는 그러한 일.
* ~을 맺다(속삭이다).
I I
사랑2 【舍廊】
[명사]
바깥주인이 거처하며 손님을 대접하는 곳. 곧 사랑채나 사랑방. = 객간.
⇔ 내실1 (內室)1."""


def test_standard_korean_entry_keeps_its_own_numbers_and_bullets():
    blocks = parse_gloss(STANDARD_SARANG)

    assert types(blocks) == [
        "section",  # I -- the homograph divider
        "headword",  # 사랑1
        "pos",  # [명사]
        "sense",  # 1 아끼고...
        "example",  # * 어머님의 ~.
        "example",  # *나라와 겨레에 대한 ~.
        "sense",  # 2 남녀가...
        "example",  # * ~을 맺다(속삭이다).
        "section",  # I I
        "headword",  # 사랑2 【舍廊】
        "pos",  # [명사]
        "sense",  # 바깥주인이... (a Hangul definition, not an example)
        "xref",  # ⇔ 내실1
    ]
    assert blocks[3]["n"] == 1
    assert blocks[3]["text"].startswith("아끼고")
    assert blocks[6]["n"] == 2
    assert blocks[12]["text"] == "내실1 (內室)1."


def test_standard_korean_definition_line_is_a_sense_not_an_example():
    "A long Hangul definition follows a POS badge; only naver examples pair."
    blocks = parse_gloss(STANDARD_SARANG)

    assert blocks[11]["t"] == "sense"
    assert blocks[11]["n"] == 1
    assert blocks[11]["text"].startswith("바깥주인이")


def test_korean_pos_badge_with_conjugation_type_stays_whole():
    blocks = parse_gloss("""가다1
[자동사]〖거라불규칙/가거라〗
1 	(이곳에서) 다른 곳으로 옮다(이동하다).""")

    assert blocks[0]["t"] == "headword"
    assert blocks[1] == {"t": "pos", "text": "[자동사]〖거라불규칙/가거라〗"}
    assert blocks[2]["t"] == "sense"


# ---------------------------------------------------------------------------
# Russian 俄汉汉俄: circled-number grammatical runs
# ---------------------------------------------------------------------------


def test_russian_circled_run_becomes_a_grammar_note_and_numbered_senses():
    gloss = (
        ", -а或-у，复дом`а〔阳〕⑴房子，房屋.  дерев`янный ～木头房."
        " ⑵住宅，住所，家. разойт`ись по ～`ам各回各家."
        " ⑶家，全家人；家务. всем ～ом 全家都到…去."
    )
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["note", "sense", "sense", "sense"]
    assert blocks[0]["text"] == "-а或-у，复дом`а〔阳〕"
    assert [b["n"] for b in blocks[1:]] == [1, 2, 3]
    assert blocks[1]["text"].startswith("房子，房屋")


def test_russian_entry_opens_with_a_pos_prefixed_translation():
    gloss = "名词 书\n簿\n册"
    blocks = parse_gloss(gloss)

    assert types(blocks) == ["pos", "sense", "sense", "sense"]
    assert blocks[0]["text"] == "名词"
    assert blocks[1] == {"t": "sense", "text": "书", "n": 1}


# --- 西汉 (Spanish-Chinese) entries ------------------------------------------


def test_a_spanish_gender_label_line_is_a_pos_badge():
    blocks = parse_gloss("casa\nf.\n房子,家,家务,商号,社,所,顾主")

    assert types(blocks) == ["sense", "pos", "sense"]
    assert blocks[0]["text"] == "casa"
    assert blocks[1] == {"t": "pos", "text": "f."}
    assert blocks[2] == {"t": "sense", "text": "房子,家,家务,商号,社,所,顾主", "n": 1}


def test_a_spanish_entry_after_headword_strip_is_a_pos_and_its_sense():
    "The repeated headword lines come off in strip_duplicate_headword."
    blocks = parse_gloss("f.\n房子,家,家务")

    assert types(blocks) == ["pos", "sense"]
    assert blocks[1] == {"t": "sense", "text": "房子,家,家务", "n": 1}


# --- 汉德 (Chinese-German) entries -------------------------------------------


HANDE_XUEXI = """学习
[xue2 xi2]
[ Substantiv ]
Studium (n)
我在大学学习中文。
wo3 zai4 da4 xue2 xue2 xi2 zhong1 wen2 .
Ich studiere an der Uni Sinologie.
[ Verb ]
<Psychologie> lernen
活到老，学习到老。
huo2 dao4 lao3 , xue2 xi2 dao4 lao3 .
lebenslang lernen
lernen; studieren"""


def test_hande_entry_gets_german_pos_meanings_and_example_triples():
    blocks = parse_gloss(HANDE_XUEXI)

    assert types(blocks) == [
        "sense",  # 学习 (dropped at lookup by strip_duplicate_headword)
        "phonetic",  # [xue2 xi2]
        "pos",  # Substantiv
        "sense",  # Studium (n)
        "example",  # 我在大学学习中文。
        "note",  # the pinyin
        "pos",  # Verb
        "sense",  # <Psychologie> lernen
        "example",  # 活到老，学习到老。
        "note",  # the pinyin
        "sense",  # lernen; studieren
    ]
    assert blocks[2]["text"] == "Substantiv"
    assert blocks[4]["text"] == "我在大学学习中文。"
    assert blocks[4]["translation"] == "Ich studiere an der Uni Sinologie."
    assert blocks[5]["text"].startswith("wo3 zai4")
    assert blocks[8]["translation"] == "lebenslang lernen"


def test_a_short_latin_line_before_a_chinese_sentence_is_a_meaning():
    '"Studium (n)" is the dictionary\'s German meaning, not a kanji example.'
    blocks = parse_gloss("[ Substantiv ]\nStudium (n)\n我在大学学习中文。")

    assert blocks[0] == {"t": "pos", "text": "Substantiv"}
    assert blocks[1] == {"t": "sense", "text": "Studium (n)", "n": 1}
    assert blocks[2]["t"] == "example"


def test_a_hande_meaning_pair_runs_into_a_final_summary_sense():
    "lernen; studieren after the example triple is the word's summary."
    blocks = parse_gloss(HANDE_XUEXI)

    assert blocks[-1] == {"t": "sense", "text": "lernen; studieren", "n": 2}


# --- 三省堂大辞林 / 小学館日中 entries ---------------------------------------


def test_daijirin_reading_pair_becomes_one_headword_line():
    blocks = parse_gloss(
        "べん－きょう―キヤウ　[0]\n【勉強】\n(名)スル(1)学問や技芸を学ぶこと。"
    )

    assert types(blocks) == ["headword", "sense"]
    assert blocks[0]["text"] == "べん－きょう―キヤウ [0]【勉強】"
    assert blocks[1]["text"].startswith("(名)スル")


def test_shougakukan_dollar_examples_carry_both_halves():
    blocks = parse_gloss(
        "（1）〔学習〕学习\n$一夜漬けの勉強／临阵磨枪\n$化学を勉強する／学习化学."
    )

    assert types(blocks) == ["sense", "example", "example"]
    assert blocks[1]["text"] == "一夜漬けの勉強"
    assert blocks[1]["translation"] == "临阵磨枪"
    assert blocks[2] == {
        "t": "example",
        "text": "化学を勉強する",
        "translation": "学习化学.",
    }
