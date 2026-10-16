"""
lute.term.pos_tags tests.

Pure string/format logic, so nothing here needs a parser or a database.
"""

from lute.term import pos_tags


# A real Sudachi POS tuple, for 出そう in 「声を出そうとしましたが」.
VERB_POS = ("動詞", "非自立可能", "*", "*", "五段-サ行", "意志推量形")


def test_pos_to_text_keeps_every_slot_including_empty_ones():
    """
    The stored form is the tuple verbatim, so that the tag format can
    be changed later without re-parsing a book.
    """
    assert pos_tags.pos_to_text(VERB_POS) == "動詞|非自立可能|*|*|五段-サ行|意志推量形"


def test_pos_to_text_round_trips_through_text_to_pos():
    pos = ("名詞", "普通名詞", "一般", "*", "*", "*")
    assert pos_tags.text_to_pos(pos_tags.pos_to_text(pos)) == pos


def test_pos_to_text_is_none_when_there_is_nothing_to_store():
    assert pos_tags.pos_to_text(None) is None
    assert pos_tags.pos_to_text(()) is None
    assert pos_tags.pos_to_text(("*", "*", "*")) is None


def test_text_to_pos_is_empty_tuple_for_missing_text():
    assert pos_tags.text_to_pos(None) == ()
    assert pos_tags.text_to_pos("") == ()


def test_tag_from_pos_drops_the_empty_slots():
    assert pos_tags.tag_from_pos(VERB_POS) == "動詞/非自立可能/五段-サ行/意志推量形"
    assert pos_tags.tag_from_pos(("名詞", "普通名詞", "一般", "*", "*", "*")) == "名詞/普通名詞/一般"
    assert pos_tags.tag_from_pos(("代名詞", "*", "*", "*", "*", "*")) == "代名詞"


def test_tag_from_pos_is_none_when_only_empty_slots():
    assert pos_tags.tag_from_pos(None) is None
    assert pos_tags.tag_from_pos(("*", "*")) is None


def test_longest_real_tag_is_why_tgtext_was_widened():
    """
    tags.TgText declared VARCHAR(20) until migration
    20261016_add_term_pos.sql.  The longest tag the JLPT corpus
    produces is 23 characters, so the declared width is now 50.
    """
    pos = ("動詞", "非自立可能", "*", "*", "文語四段-ラ行", "連体形-一般")
    tag = pos_tags.tag_from_pos(pos)
    assert tag == "動詞/非自立可能/文語四段-ラ行/連体形-一般"
    assert len(tag) == 23
