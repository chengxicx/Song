"""
Tests for the shadowing (read-aloud) scoring feature.

The real faster-whisper dependency is never loaded: transcribe_clip is
monkeypatched (or given a fake model), and the tests cover the token
diff and the /read/shadowing/transcribe route.
"""

import io
import json
import os
import time
from unittest.mock import patch

import pytest

from lute.book import sensevoice
from lute.db import db
from lute.read import shadowing

from tests.utils import add_terms


@pytest.fixture(autouse=True)
def _no_real_model_load():
    """
    Never load a real model: tasks load them up front.  SenseVoice is
    also disabled by default (no sherpa-onnx/model files on CI); tests
    covering that path override it.
    """
    with patch.object(shadowing, "_load_model", return_value=object()), patch.object(
        sensevoice, "available", return_value=False
    ):
        yield


def _wait_for_task(task_id, timeout=10.0):
    "Poll a task until it reaches a terminal state."
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = shadowing.task_status(task_id)
        if status["state"] in ("finished", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish in time: {status}")


# ---------------------------------------------------------------------
# Duck-typed Language for the diff tests: the spoken tokens are fixed
# per instance, so tests control exactly what whisper "heard".
# ---------------------------------------------------------------------


class _FakeParsedToken:
    def __init__(self, token, is_word=True):
        self.token = token
        self.is_word = is_word


class _FakeParser:
    "Duck-typed parser: fixed readings + plain lowercase."

    def __init__(self, readings=None, multi_readings=None, context_readings=None):
        self.readings = readings or {}
        self.multi_readings = multi_readings or {}
        # [(surface, reading-or-None)] a get_context_readings call answers,
        # or None to simulate a parser without contextual readings.
        self.context_readings = context_readings
        self.context_calls = []

    def get_reading(self, text):
        return self.readings.get(text)

    def get_readings(self, text):
        return self.multi_readings.get(text) or []

    def get_context_readings(self, text):
        self.context_calls.append(text)
        return self.context_readings

    def get_lowercase(self, text):
        return text.lower()


class _FakeLanguage:
    "Duck-typed Language.  Entries may be strings or (token, is_word)."

    def __init__(
        self,
        spoken_tokens,
        parser_type="spacedel",
        readings=None,
        multi_readings=None,
        context_readings=None,
    ):
        self.parser_type = parser_type
        self.spoken_tokens = spoken_tokens
        self._parser = _FakeParser(readings, multi_readings, context_readings)

    @property
    def parser(self):
        return self._parser

    def get_parsed_tokens(self, _text):
        toks = []
        for t in self.spoken_tokens:
            if isinstance(t, tuple):
                toks.append(_FakeParsedToken(t[0], t[1]))
            else:
                toks.append(_FakeParsedToken(t))
        return toks


# ---------------------------------------------------------------------
# Token diff
# ---------------------------------------------------------------------


def test_perfect_match():
    lang = _FakeLanguage(["The", "calm", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "The calm cat.", lang)
    assert res["statuses"] == [2, 2, 2]
    assert res["score"] == 100
    assert res["extras"] == []


def test_missed_word():
    "A skipped word stays a miss; the score rounds to the nearest percent."
    lang = _FakeLanguage(["the", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the cat", lang)
    assert res["statuses"] == [2, 0, 2]
    assert res["score"] == 67


def test_nothing_spoken_marks_all_missed():
    lang = _FakeLanguage([])
    res = shadowing.compare_tokens(["the", "cat"], "", lang)
    assert res["statuses"] == [0, 0]
    assert res["score"] == 0


def test_misread_word_is_fuzzy():
    "A near-match token (ratio >= 0.6) counts as a misread attempt."
    lang = _FakeLanguage(["the", "cam", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "Cat"], "the cam cat", lang)
    assert res["statuses"] == [2, 1, 2]
    assert res["spoken_for_fuzzy"] == {1: "cam"}
    assert res["score"] == 83


def test_replace_below_ratio_is_miss():
    "A token that is too far from anything spoken is just a miss."
    lang = _FakeLanguage(["the", "dog", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the dog cat", lang)
    assert res["statuses"] == [2, 0, 2]
    assert res["spoken_for_fuzzy"] == {}


def test_miss_records_what_was_heard():
    """
    A too-far pair is still a miss, but it reports the token that was
    heard: the panel draws it as "-> heard" so the learner can see what
    they actually said.
    """
    lang = _FakeLanguage(["the", "dog", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the dog cat", lang)
    assert res["statuses"] == [2, 0, 2]
    assert res["spoken_for_fuzzy"] == {}
    assert res["spoken_for_miss"] == {1: "dog"}


def test_unspoken_word_has_no_heard_entry():
    "A word that was never spoken has no counterpart to report."
    lang = _FakeLanguage(["the", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the cat", lang)
    assert res["statuses"] == [2, 0, 2]
    assert res["spoken_for_miss"] == {}


def test_rescued_miss_drops_the_heard_entry():
    """
    The containment pass can rescue a word the positional pairing had
    called a miss (the word glued into a longer chunk).  It is a match
    then, and the stale miss readout must not survive.
    """
    lang = _FakeLanguage(["foobarxyz"])
    res = shadowing.compare_tokens(["foo", "bar"], "foobarxyz", lang)
    assert res["statuses"] == [2, 2]
    assert res["spoken_for_miss"] == {}


def test_split_spoken_tokens_join_to_a_match():
    """
    Tokenization drift, not a misread: the page's term 見たい comes back
    as the parser's 見 + たい.  The exact join of the spoken keys is the
    original's key, so the word is a match and there is no bogus extra.
    """
    lang = _FakeLanguage(
        ["そう", "です", "か", "日本", "の", "桜", "が", "見", "たい", "です", "ね"],
        parser_type="japanese",
        readings={"見たい": "みたい", "見": "み", "たい": "たい"},
    )
    res = shadowing.compare_tokens(
        ["そう", "です", "か", "日本", "の", "桜", "が", "見たい", "です", "ね"],
        "そう です か 日本 の 桜 が 見たい です ね",
        lang,
    )
    assert res["statuses"] == [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
    assert res["extras"] == []
    assert res["score"] == 100


def test_merged_spoken_token_splits_to_a_match():
    "Mirror drift: two page terms come back as one parser token."
    lang = _FakeLanguage(["the", "calmcat"], readings={})
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the calmcat", lang)
    assert res["statuses"] == [2, 2, 2]
    assert res["extras"] == []
    assert res["score"] == 100


def test_several_merged_spoken_tokens_join_to_matches():
    """
    Drift across several words at once: the sentence's 呢 + 个 + 系 + 阿乐
    comes back as the parser's 呢个 + 系阿乐 (pycantonese re-cuts particle
    + name).  Each run whose joined keys agree is a match, not a pile of
    misses with a bogus extra.
    """
    lang = _FakeLanguage(["呢个", "系阿乐"])
    res = shadowing.compare_tokens(["呢", "个", "系", "阿乐"], "呢个 系阿乐", lang)
    assert res["statuses"] == [2, 2, 2, 2]
    assert res["extras"] == []
    assert res["score"] == 100


def test_drift_runs_leave_a_genuinely_unspoken_word_a_miss():
    "Words absorbed by a drift run match; a different word after stays judged."
    lang = _FakeLanguage(["呢个", "系阿乐", "唔该"])
    res = shadowing.compare_tokens(["呢", "个", "系", "阿乐", "唔好"], "呢个 系阿乐 唔该", lang)
    assert res["statuses"] == [2, 2, 2, 2, 0]
    # 唔该 was paired against 唔好 as its attempted misread, so it is
    # not also counted as an extra.
    assert res["spoken_for_fuzzy"] == {}
    assert res["extras"] == []


def test_voicing_swap_is_fuzzy_not_miss():
    "か heard as が is the classic ASR voicing swap: half credit."
    lang = _FakeLanguage(["そう", "です", "が"], parser_type="japanese")
    res = shadowing.compare_tokens(["そう", "です", "か"], "そう です が", lang)
    assert res["statuses"] == [2, 2, 1]
    assert res["spoken_for_fuzzy"] == {2: "が"}
    assert res["score"] == 83


def test_voicing_swap_never_rescues_unrelated_words():
    "Stripping dakuten must not make genuinely different words match."
    lang = _FakeLanguage(["cat"], parser_type="japanese")
    res = shadowing.compare_tokens(["dog"], "cat", lang)
    assert res["statuses"] == [0]
    assert res["score"] == 0


def test_hangul_to_romaja():
    "Unicode composes each syllable from jamo indices; no dictionary needed."
    assert shadowing._hangul_to_romaja("선배") == "seonbae"
    assert shadowing._hangul_to_romaja("한국") == "hanguk"
    assert shadowing._hangul_to_romaja("です") == "です"
    assert shadowing._hangul_to_romaja("선배べ") == "seonbaeべ"


def test_japanese_korean_cognate_leakage_is_fuzzy():
    """
    SenseVoice trains Japanese and Korean on one model, and a Japanese
    word with a Korean cognate can come back in hangul (先輩 せんぱい ->
    선배).  Romaja against the reading's romaji makes it the near-miss it
    sounds like, with the hangul shown as what was heard.
    """
    lang = _FakeLanguage(
        ["上田", "선배", "です"],
        parser_type="japanese",
        readings={"先輩": "せんぱい"},
    )
    res = shadowing.compare_tokens(["上田", "先輩", "です"], "上田 선배 です", lang)
    assert res["statuses"] == [2, 1, 2]
    assert res["spoken_for_fuzzy"] == {1: "선배"}
    assert res["score"] == 83


def test_japanese_korean_leakage_with_a_different_sound_stays_a_miss():
    "The romaja rescue must not paper over genuinely different sounds."
    lang = _FakeLanguage(["방"], parser_type="japanese", readings={"犬": "いぬ"})
    res = shadowing.compare_tokens(["犬"], "방", lang)
    assert res["statuses"] == [0]
    assert res["score"] == 0


def test_chinese_misread_by_sound_is_fuzzy_not_miss():
    """
    A Chinese misread is a different character, so the surface forms
    share nothing and the plain ratio is 0.  The parser's romanization
    decides: 你 (nei5) read as 李 (lei5) is the near-miss it sounds like.
    """
    lang = _FakeLanguage(["李"], readings={"你": "nei5", "李": "lei5"})
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["你"], "李", lang)
    assert res["statuses"] == [1]
    assert res["spoken_for_fuzzy"] == {0: "李"}
    assert res["score"] == 50


def test_chinese_different_sound_stays_a_miss():
    "The romanization rescue must not paper over genuinely different syllables."
    lang = _FakeLanguage(["嘅"], readings={"你": "nei5", "嘅": "ge3"})
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["你"], "嘅", lang)
    assert res["statuses"] == [0]
    assert res["score"] == 0


def test_pinyin_tone_marks_count_for_the_sound_rescue():
    "pypinyin's marked vowels fold to digit tones before comparing."
    lang = _FakeLanguage(["骂"], readings={"妈": "mā", "骂": "mà"})
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["妈"], "骂", lang)
    assert res["statuses"] == [1]
    assert res["spoken_for_fuzzy"] == {0: "骂"}


def test_han_numeral_run_values():
    "Digits concatenate, units multiply and add; no digits before a unit means one."
    f = shadowing._han_numeral_run_value
    assert f("二十") == 20
    assert f("二千") == 2000
    assert f("两千零二十五") == 2025
    assert f("二〇二五") == 2025
    assert f("十九") == 19
    assert f("一千万") == 10000000
    assert f("二万五千") == 25000


def test_chinese_arabic_numerals_match_han_numerals():
    """
    SenseVoice's ITN writes numbers as digits: 二千/二十 come back as
    2000/20.  The diff keys fold Han numeral runs to their value, so the
    鲁迅 sentence's 二千馀里 matches the transcribed 2000余里, variant
    馀 included.
    """
    lang = _FakeLanguage(["我", "冒", "了", "严寒", "回到", "相隔", "2000", "余", "里"])
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(
        ["我", "冒", "了", "严寒", "回到", "相隔", "二千", "馀", "里"],
        "我冒了严寒回到相隔2000余里",
        lang,
    )
    assert res["statuses"] == [2] * 9
    assert res["score"] == 100


def test_chinese_digit_by_digit_year_folds_to_the_same_value():
    "The digit-by-digit year reading and the cardinal share the value."
    lang = _FakeLanguage(["2025"])
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(["二〇二五"], "2025", lang)
    assert res["statuses"] == [2]


def test_chinese_homophone_heard_is_a_match():
    """
    The engine cannot tell homophones apart: 观潮 (guāncháo) answered as
    官潮 is the sound said exactly, so a full match -- not the near-miss
    the romanization rescue used to cap at.
    """
    lang = _FakeLanguage(["官潮"], readings={"观潮": "guān cháo", "官潮": "guān cháo"})
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(["观潮"], "官潮", lang)
    assert res["statuses"] == [2]
    assert res["spoken_for_fuzzy"] == {}


def test_chinese_variant_yu_is_a_match_not_a_near_miss():
    """
    馀 is the simplified variant of 餘 (余) that opencc's t2s table
    misses: the book's 馀 against the engine's 余 is the same word, a
    full match -- not the same-pinyin near-miss the sound rescue gave.
    """
    lang = _FakeLanguage(["余"])
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(["馀"], "余", lang)
    assert res["statuses"] == [2]
    assert res["spoken_for_fuzzy"] == {}


def test_chinese_single_chars_glued_into_a_heard_chunk_match():
    """
    The engine answers 馀 + 里 with the parser's single token 余里: each
    Han character is a whole word, so its verbatim occurrence inside the
    glued chunk is the word said -- matches, not syllable half-credit.
    """
    lang = _FakeLanguage(["相隔", "二", "0", "余里"])
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(["相隔", "二千", "馀", "里"], "相隔 二 0 余里", lang)
    # 二千 was only partially captured (二 0): an attempted number.
    assert res["statuses"] == [2, 1, 2, 2]
    assert res["spoken_for_fuzzy"] == {1: "二"}


def test_chinese_partial_numeric_capture_is_fuzzy():
    "The engine's 二 0 for 二千 lands some digits: attempted, half credit."
    lang = _FakeLanguage(["二"])
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(["二千"], "二", lang)
    assert res["statuses"] == [1]
    assert res["spoken_for_fuzzy"] == {0: "二"}


def test_chinese_unrelated_digit_stays_a_miss():
    "A digit that is no prefix of the target's value is still a miss."
    lang = _FakeLanguage(["0"])
    lang.tts_lang = "zh"
    res = shadowing.compare_tokens(["二千"], "0", lang)
    assert res["statuses"] == [0]


def test_word_left_inside_a_glued_spoken_token_is_a_match():
    """
    The engines can glue adjacent words into one chunk that is not the
    exact join of their keys: 拍手 + 大聲笑 heard as the non-word
    拍笑大聲笑.  The 1:1 pairing consumes the whole chunk on 拍手, which
    would leave 大聲笑 -- verbatim inside the chunk -- a flat miss; the
    containment pass finds it.  拍手 itself shares only its first
    syllable with the chunk and stays a miss.
    """
    lang = _FakeLanguage(
        ["同學", "仔", "都", "拍笑大聲笑"],
        readings={
            "拍手": "paak3 sau2",
            "拍笑大聲笑": "paak3 siu3 daai6 sing1 siu3",
        },
    )
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(
        ["同學", "仔", "都", "拍手", "大聲笑"],
        "同學仔都拍笑大聲笑",
        lang,
    )
    assert res["statuses"] == [2, 2, 2, 0, 2]
    assert res["extras"] == []
    assert res["score"] == 80


def test_leftover_word_rescued_by_syllables_inside_a_merged_token():
    """
    A left-behind word need not appear in the chunk to count: 夠 + 喇
    heard as the single token 夠剌.  夠 is verbatim in the chunk, so it
    is a match; 喇 is not, but its syllable is in the chunk's reading
    (gau3 laa1), so it is the near-miss it sounds like instead of a
    skip.
    """
    lang = _FakeLanguage(["夠剌"], readings={"夠": "gau3", "喇": "laa3", "夠剌": "gau3 laa1"})
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["夠", "喇"], "夠剌", lang)
    assert res["statuses"] == [2, 1]
    assert res["spoken_for_fuzzy"] == {1: "夠剌"}
    assert res["score"] == 75


def test_chinese_rescue_judges_on_every_reading_the_parser_offers():
    """
    The parser's single dictionary pick can be the wrong sense of a
    polyphone: 阿 filed under o1 misses that the name prefix is read
    aa3.  With every reading offered, 阿明答 heard as 亞面達 is the
    near-miss it sounds like; with only the dictionary pick it is a
    flat miss (next test).
    """
    lang = _FakeLanguage(
        ["亞面達"],
        multi_readings={
            "阿明答": ["o1 ming4 daap3", "aa3 ming4 daap3"],
            "亞面達": ["aa3 min6 daat6"],
        },
    )
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["阿明答"], "亞面達", lang)
    assert res["statuses"] == [1]
    assert res["spoken_for_fuzzy"] == {0: "亞面達"}
    assert res["score"] == 50


def test_chinese_rescue_without_a_matching_reading_stays_a_miss():
    "Offering readings must not paper over a genuinely different sound."
    lang = _FakeLanguage(
        ["亞面達"],
        multi_readings={
            "阿明答": ["o1 ming4 daap3"],
            "亞面達": ["aa3 min6 daat6"],
        },
    )
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["阿明答"], "亞面達", lang)
    assert res["statuses"] == [0]
    assert res["score"] == 0


def test_containment_pass_needs_every_syllable_of_the_word():
    "A chunk sharing only part of the word is not the word: no rescue."
    lang = _FakeLanguage(
        ["你聽"],
        readings={"你好": "nei5 hou2", "你聽": "nei5 teng1"},
    )
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(["你好"], "你聽", lang)
    assert res["statuses"] == [0]
    assert res["score"] == 0


def test_extra_spoken_words():
    lang = _FakeLanguage(["the", "very", "calm", "cat"])
    res = shadowing.compare_tokens(["The", "calm", "cat"], "the very calm cat", lang)
    assert res["statuses"] == [2, 2, 2]
    assert res["extras"] == ["very"]


def test_japanese_matches_by_reading():
    "良い spoken as いい is the correct reading of the kanji, not a miss."
    lang = _FakeLanguage(
        ["今日", "は", "いい", "天気", "です"],
        parser_type="japanese",
        readings={"良い": "いい", "天気": "テンキ"},
    )
    res = shadowing.compare_tokens(["今日", "は", "良い", "天気", "です"], "今日はいい天気です", lang)
    assert res["statuses"] == [2, 2, 2, 2, 2]
    assert res["score"] == 100


def test_japanese_reading_katakana_normalized():
    "IPADIC-style katakana readings compare equal to hiragana speech."
    lang = _FakeLanguage(["てんき"], parser_type="japanese", readings={"天気": "テンキ"})
    res = shadowing.compare_tokens(["天気"], "てんき", lang)
    assert res["statuses"] == [2]


def test_japanese_kana_only_word_matches_across_scripts():
    """
    A kana word is its own reading: the parser answers None for it
    (Sudachi only reports a reading when it differs from the surface),
    so the surface must be folded as well.  A book's わくわく heard as
    ワクワク is the word that was said, not a missed one -- the two
    unfoldable keys share no characters at all.
    """
    lang = _FakeLanguage(["ワクワク"], parser_type="japanese")
    res = shadowing.compare_tokens(["わくわく"], "ワクワク", lang)
    assert res["statuses"] == [2]
    assert res["score"] == 100


def test_japanese_katakana_book_word_matches_hiragana_speech():
    "The fold is not one-directional: a katakana word is heard as kana too."
    lang = _FakeLanguage(["わくわく"], parser_type="japanese")
    res = shadowing.compare_tokens(["ワクワク"], "わくわく", lang)
    assert res["statuses"] == [2]
    assert res["score"] == 100


def test_japanese_kana_fold_does_not_merge_different_words():
    "Folding the script must not make unrelated kana words compare equal."
    lang = _FakeLanguage(["ドキドキ"], parser_type="japanese")
    res = shadowing.compare_tokens(["わくわく"], "ドキドキ", lang)
    assert res["statuses"] == [0]
    assert res["score"] == 0


def test_japanese_kana_fold_survives_the_context_parse():
    """
    A kana token gets no reading from the contextual parse either ("a
    kana token's own kana is no furigana"), so _context_keys falls back
    to the per-token key -- which has to fold the script as well.
    """
    lang = _FakeLanguage(
        ["ワクワク"],
        parser_type="japanese",
        context_readings=[("わくわく", "わくわく")],
    )
    res = shadowing.compare_tokens(["わくわく"], "ワクワク", lang, original_full_text="わくわく")
    assert res["statuses"] == [2]
    assert res["score"] == 100


def test_japanese_keys_follow_the_displayed_context_readings():
    """
    The panel's furigana is the contextual parse (香山 is こうやま in its
    sentence, かやま alone); scoring must judge against the same reading.
    Heard as 高山 (こうざん), the word is the half-credit misread it
    looks like against こうやま -- not a flat miss against かやま.
    """
    lang = _FakeLanguage(
        ["高山", "へ"],
        parser_type="japanese",
        readings={"香山": "かやま", "高山": "こうざん"},
        context_readings=[("香山", "こうやま"), ("へ", "へ")],
    )
    res = shadowing.compare_tokens(["香山", "へ"], "高山へ", lang, original_full_text="香山へ")
    assert res["statuses"] == [1, 2]
    assert res["spoken_for_fuzzy"] == {0: "高山"}


def test_japanese_keys_stay_per_token_without_the_sentence():
    "Old clients send no full_text: the isolated readings decide, as before."
    lang = _FakeLanguage(
        ["高山", "へ"],
        parser_type="japanese",
        readings={"香山": "かやま", "高山": "こうざん"},
        context_readings=[("香山", "こうやま"), ("へ", "へ")],
    )
    res = shadowing.compare_tokens(["香山", "へ"], "高山へ", lang)
    assert res["statuses"] == [0, 2]


def test_japanese_heard_side_also_reads_in_context():
    """
    The transcription is a sentence too: its keys follow the same
    contextual parse the "heard" furigana is drawn from.  私 read alone
    is わたくし, but both sentences read it わたし -- the user said the
    word the panel displays, and it scores as one.
    """
    lang = _FakeLanguage(
        ["私", "は"],
        parser_type="japanese",
        readings={"私": "わたくし"},
        context_readings=[("私", "わたし"), ("は", "は")],
    )
    res = shadowing.compare_tokens(["私", "は"], "私は", lang, original_full_text="私は")
    assert res["statuses"] == [2, 2]
    assert res["score"] == 100


def test_japanese_word_inside_glued_heard_chunk_is_a_match():
    """
    The engine transcribed こうやま as the non-word chunk 紅う山 (reading
    あこうやま): the word's kana sits verbatim inside the chunk's
    reading, so the word was said -- a match, not a misread.
    """
    lang = _FakeLanguage(
        ["紅う山"],
        parser_type="japanese",
        readings={"香山": "こうやま", "紅う山": "あこうやま"},
        context_readings=[("香山", "こうやま")],
    )
    res = shadowing.compare_tokens(["香山"], "紅う山", lang, original_full_text="香山")
    assert res["statuses"] == [2]
    assert res["score"] == 100


def test_japanese_containment_rescue_needs_the_whole_word():
    "A chunk sharing only part of the word's kana is not the word."
    lang = _FakeLanguage(
        ["こうざん"],
        parser_type="japanese",
        readings={"香山": "こうやま", "高山": "こうざん"},
        context_readings=[("香山", "こうやま")],
    )
    # こうやま vs こうざん: no containment (ん never appears), ratio 0.5
    # → half-credit misread, not a match.
    res = shadowing.compare_tokens(["香山"], "こうざん", lang, original_full_text="香山")
    assert res["statuses"] == [1]
    assert res["spoken_for_fuzzy"] == {0: "こうざん"}


def test_japanese_one_mora_words_are_not_containment_matched():
    "A single-mora key must not match every chunk that happens to contain it."
    lang = _FakeLanguage(
        ["ばか"], parser_type="japanese", readings={"か": "か", "ばか": "ばか"}
    )
    # か (1 mora) inside ばか: without the length gate this would match.
    res = shadowing.compare_tokens(["か"], "ばか", lang, original_full_text="か")
    assert res["statuses"] == [1]
    assert res["spoken_for_fuzzy"] == {0: "ばか"}


def test_spoken_punctuation_ignored():
    lang = _FakeLanguage([("。", False), ("cat", True)])
    res = shadowing.compare_tokens(["cat"], "。cat", lang)
    assert res["statuses"] == [2]


def test_zws_stripped_from_tokens():
    lang = _FakeLanguage(["ca\u200Bt"])
    res = shadowing.compare_tokens(["cat"], "cat", lang)
    assert res["statuses"] == [2]


def test_empty_original_tokens_are_skipped():
    "Rendering artifacts (empty word spans) are skipped, not scored."
    lang = _FakeLanguage(["cat"])
    res = shadowing.compare_tokens(["cat", "", ""], "cat", lang)
    assert res["statuses"] == [2, 3, 3]
    assert res["total"] == 1
    assert res["score"] == 100


def test_punctuation_original_tokens_are_neutral():
    "Punctuation on the original side is never scored nor marked."
    lang = _FakeLanguage(["你好", "世界"])
    res = shadowing.compare_tokens(["你好", "。", "世界"], "你好世界", lang)
    assert res["statuses"] == [2, 3, 2]
    assert res["total"] == 2
    assert res["score"] == 100


def test_spoken_punctuation_dropped_even_if_marked_as_word():
    "A parser tagging punctuation is_word=True cannot make it scoreable."
    lang = _FakeLanguage([("。", True), ("你好", True)])
    res = shadowing.compare_tokens(["你好"], "你好。", lang)
    assert res["statuses"] == [2]
    assert res["spoken_count"] == 1
    assert res["extras"] == []


def test_number_token_outside_word_chars_is_scored():
    """
    Many languages' word characters exclude digits, so the page's 100%
    is one non-word token (' 100%," '), and the engine may answer with
    the same run.  It is still something the user said: kept on both
    sides and scored like any word, not filtered out.
    """
    lang = _FakeLanguage(
        [("give", True), (' 100%," ', False), ("she", True), ("said", True)]
    )
    res = shadowing.compare_tokens(
        ["give", "100%", "she", "said"], "give 100% she said", lang
    )
    assert res["statuses"] == [2, 2, 2, 2]
    assert res["score"] == 100


def test_number_token_misheard_counts_as_a_miss():
    """
    The take that found the filter: "my 100%" heard as "mine".  The
    number stays in the total, so the misread costs score instead of
    vanishing.
    """
    lang = _FakeLanguage(
        [("give", True), ("mine", True), ("she", True), ("said", True)]
    )
    res = shadowing.compare_tokens(
        ["give", "my", "100%", "she", "said"], "give mine she said", lang
    )
    assert res["statuses"] == [2, 0, 0, 2, 2]
    assert res["score"] == 60


def test_clean_token_trims_glued_edge_punctuation():
    "The same spoken words with different glued punctuation compare equal."
    assert shadowing._clean_token(' 100%," ') == "100%"
    assert shadowing._clean_token("100%") == "100%"
    assert shadowing._clean_token("(cat)") == "cat"
    assert shadowing._clean_token("。") == ""


def test_spoken_tokens_keep_nonword_digit_tokens():
    "A non-word token with digits survives transcription parsing."
    lang = _FakeLanguage([(' 100%," ', False), ("cat", True)])
    assert shadowing._spoken_tokens("ignored", lang) == ["100%", "cat"]


def test_all_punctuation_originals_score_100():
    lang = _FakeLanguage(["你好"])
    res = shadowing.compare_tokens(["。", "、"], "你好", lang)
    assert res["statuses"] == [3, 3]
    assert res["total"] == 0
    assert res["score"] == 100


def test_empty_original_tokens_returns_neutral_result():
    lang = _FakeLanguage(["cat"])
    res = shadowing.compare_tokens([], "cat", lang)
    assert res["statuses"] == []
    assert res["spoken_count"] == 1


def test_compare_tokens_accepts_preparsed_spoken_tokens():
    "The caller's already-parsed tokens are reused instead of re-parsing."
    lang = _FakeLanguage(["the", "cat"])
    res = shadowing.compare_tokens(
        ["The", "cat"], "the cat", lang, spoken_tokens=["the", "cat"]
    )
    assert res["statuses"] == [2, 2]
    assert res["score"] == 100


# ---------------------------------------------------------------------
# Furigana annotation
# ---------------------------------------------------------------------


def test_annotate_tokens_japanese_includes_readings():
    lang = _FakeLanguage(
        [],
        parser_type="japanese",
        readings={"天気": "てんき", "良い": "いい"},
    )
    annotated = shadowing.annotate_tokens(["天気", "は", "良い"], lang)
    assert annotated == [
        {"text": "天気", "reading": "てんき"},
        {"text": "は", "reading": None},
        {"text": "良い", "reading": "いい"},
    ]


def test_annotate_tokens_non_japanese_has_no_readings():
    lang = _FakeLanguage([], readings={"Where": "どこ"})
    annotated = shadowing.annotate_tokens(["Where", "are"], lang)
    assert annotated == [
        {"text": "Where", "reading": None},
        {"text": "are", "reading": None},
    ]


def test_annotate_tokens_strips_zws_from_text():
    lang = _FakeLanguage([], parser_type="japanese", readings={})
    annotated = shadowing.annotate_tokens(["ca\u200Bt"], lang)
    assert annotated == [{"text": "cat", "reading": None}]


# The morphemes a Japanese parser would answer for 広い宇宙の数ある一つ
# when it reads the whole sentence: 数 is かず here, and 一つ splits into
# 一(ひと) + つ.
_CONTEXT_READINGS = [
    ("広い", "ひろい"),
    ("宇宙", "うちゅう"),
    ("の", None),
    ("、", None),
    ("数", "かず"),
    ("ある", None),
    ("一", "ひと"),
    ("つ", None),
]


def test_annotate_tokens_full_text_reads_in_context():
    "With the sentence, readings come from the contextual parse."
    lang = _FakeLanguage([], parser_type="japanese", context_readings=_CONTEXT_READINGS)
    annotated = shadowing.annotate_tokens(
        ["広い", "宇宙", "の", "数", "ある", "一", "つ"],
        lang,
        full_text="広い宇宙の、数ある一つ",
    )
    assert annotated == [
        {"text": "広い", "reading": "ひろい"},
        {"text": "宇宙", "reading": "うちゅう"},
        {"text": "の", "reading": None},
        {"text": "数", "reading": "かず"},
        {"text": "ある", "reading": None},
        {"text": "一", "reading": "ひと"},
        {"text": "つ", "reading": None},
    ]


def test_annotate_tokens_context_merges_split_morphemes():
    "A panel token spanning several morphemes gets their joined reading."
    lang = _FakeLanguage(
        [], parser_type="japanese", context_readings=[("一", "ひと"), ("つ", "つ")]
    )
    annotated = shadowing.annotate_tokens(["一つ"], lang, full_text="一つ")
    assert annotated == [{"text": "一つ", "reading": "ひとつ"}]


def test_annotate_tokens_context_mismatch_falls_back_per_token():
    "Tokens the morphemes can't rebuild use the per-token readings."
    lang = _FakeLanguage(
        [],
        parser_type="japanese",
        readings={"数": "すう"},
        context_readings=[("完全に", "別の")],
    )
    annotated = shadowing.annotate_tokens(["数"], lang, full_text="数")
    assert annotated == [{"text": "数", "reading": "すう"}]


def test_annotate_tokens_no_full_text_skips_context_parse():
    lang = _FakeLanguage(
        [],
        parser_type="japanese",
        readings={"天気": "てんき"},
        context_readings=[("天気", "てんき")],
    )
    annotated = shadowing.annotate_tokens(["天気"], lang)
    assert annotated == [{"text": "天気", "reading": "てんき"}]
    assert lang.parser.context_calls == []


def test_annotate_tokens_context_parse_error_falls_back_per_token():
    def _boom(_text):
        raise RuntimeError("parser broke")

    lang = _FakeLanguage([], parser_type="japanese", readings={"数": "かず"})
    lang.parser.get_context_readings = _boom
    annotated = shadowing.annotate_tokens(["数"], lang, full_text="数")
    assert annotated == [{"text": "数", "reading": "かず"}]


def test_annotate_tokens_non_japanese_ignores_full_text():
    lang = _FakeLanguage([], readings={"Where": "どこ"})
    annotated = shadowing.annotate_tokens(["Where"], lang, full_text="Where")
    assert annotated == [{"text": "Where", "reading": None}]
    assert lang.parser.context_calls == []


# ---------------------------------------------------------------------
# transcribe_clip (fake model; faster-whisper is never imported)
# ---------------------------------------------------------------------


def test_transcribe_clip_uses_greedy_decoding_without_word_timestamps():
    "Greedy + no word timestamps + no initial_prompt (echoing would inflate scores)."
    captured = {}

    class _Seg:
        text = " Hello world."
        words = None

    class _Info:
        duration = 1.5

    class _FakeModel:
        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            return iter([_Seg()]), _Info()

    with patch.object(shadowing, "_load_model", return_value=_FakeModel()):
        text, duration = shadowing.transcribe_clip("/tmp/x.wav", "en", "small")

    assert captured["beam_size"] == 1
    # The diff only needs the text; the alignment pass would only add
    # CPU time to an already CPU-bound step.
    assert captured["word_timestamps"] is False
    assert captured["language"] == "en"
    assert "initial_prompt" not in captured
    assert text == "Hello world."
    assert duration == 1.5


# ---------------------------------------------------------------------
# Route validation
# ---------------------------------------------------------------------


def test_route_requires_an_engine(app, client, english):
    "No engine at all: SenseVoice unavailable AND whisper not installed."
    with patch.object(sensevoice, "lang_code_for", return_value="en"), patch.object(
        sensevoice, "available", return_value=False
    ), patch.object(shadowing, "whisper_status", return_value={"installed": False}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={"language_id": str(english.id), "tokens": "[]"},
        )
    assert resp.status_code == 400
    assert "No transcription engine" in resp.get_json()["error"]


def test_route_sensevoice_counts_as_an_engine(app, client, english):
    "SenseVoice primary: available() alone satisfies the pre-check."
    with patch.object(sensevoice, "lang_code_for", return_value="en"), patch.object(
        sensevoice, "available", return_value=True
    ), patch.object(shadowing, "whisper_status", return_value={"installed": False}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={"language_id": str(english.id), "tokens": "[]"},
        )
    assert resp.status_code == 400
    # Past the engine gate, into the next validation.
    assert "no audio" in resp.get_json()["error"]


def test_route_requires_audio(app, client, english):
    with patch.object(shadowing, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={"language_id": str(english.id), "tokens": "[]"},
        )
    assert resp.status_code == 400
    assert "no audio" in resp.get_json()["error"]


def test_route_requires_language(app, client):
    with patch.object(shadowing, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={"tokens": "[]", "audio": (io.BytesIO(b"x"), "clip.webm")},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 400
    assert "language" in resp.get_json()["error"]


def test_route_rejects_bad_tokens(app, client, english):
    with patch.object(shadowing, "whisper_status", return_value={"installed": True}):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": "not json",
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )
    assert resp.status_code == 400
    assert "tokens" in resp.get_json()["error"]


# ---------------------------------------------------------------------
# Route happy path + temp-file lifecycle
# ---------------------------------------------------------------------


def test_route_scores_recording(app, app_context, client, english):
    "Happy path: POST returns a task id; the task scores and cleans up."
    tempdir = app.env_config.temppath

    def _fake_clip(audio_path, lang_code, model_size="small"):
        assert os.path.exists(audio_path)
        assert os.path.basename(audio_path).startswith("shadowing_")
        assert model_size == "small"
        return "The calm cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"fake webm bytes"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        task_id = resp.get_json()["task_id"]
        status = _wait_for_task(task_id)

    assert status["state"] == "finished"
    body = status["result"]
    assert body["transcription"] == "The calm cat."
    # English has SenseVoice files unavailable in the test env: whisper ran.
    assert body["engine"] == "whisper"
    assert body["language_note"] is None
    # The heard sentence is annotated token-by-token for the panel's
    # furigana + click-to-pronounce rendering (no readings for English).
    assert body["transcription_tokens"] == [
        {"text": "The", "reading": None},
        {"text": "calm", "reading": None},
        {"text": "cat", "reading": None},
    ]
    assert body["statuses"] == [2, 2, 2]
    assert body["score"] == 100
    assert body["duration"] == 6.0
    # 3 tokens over 6 seconds = 30 tokens/minute.
    assert body["tokens_per_minute"] == 30.0
    assert body["token_kind"] == "word"

    # The temp clip was cleaned up.
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("shadowing_")]
    assert leftovers == []


def test_status_endpoint_reports_unknown(app, client):
    resp = client.get("/read/shadowing/status/not-a-task")
    assert resp.status_code == 200
    assert resp.get_json()["state"] == "unknown"


def test_route_passes_full_text_to_the_scorer(app, app_context, client, english):
    "The sentence rides along to the diff, or arrives as None when absent."
    calls = []

    real = shadowing.compare_tokens

    def _spy(*args, **kwargs):
        calls.append(kwargs.get("original_full_text"))
        return real(*args, **kwargs)

    def _fake_clip(audio_path, lang_code, model_size="small"):
        return "The calm cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip), patch.object(
        shadowing, "compare_tokens", side_effect=_spy
    ):
        for full_text in ("The calm cat.", None):
            data = {
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"fake webm bytes"), "clip.webm"),
            }
            if full_text is not None:
                data["full_text"] = full_text
            resp = client.post(
                "/read/shadowing/transcribe",
                data=data,
                content_type="multipart/form-data",
            )
            assert resp.status_code == 200
            status = _wait_for_task(resp.get_json()["task_id"])
            assert status["state"] == "finished"

    assert calls == ["The calm cat.", None]


def test_route_reports_what_was_heard_for_a_miss(app, app_context, client, english):
    "A word scored as a miss still carries the heard token in the payload."

    def _fake_clip(audio_path, lang_code, model_size="small"):
        return "The dog cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"fake webm bytes"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "finished"
    assert status["result"]["spoken_for_miss"] == {"1": "dog"}


def test_route_uses_sensevoice_when_available(app, app_context, client, english):
    "A supported language transcribes via SenseVoice when it is ready."

    def _fake_sv(audio_path, lang_code):
        assert lang_code == "en"
        return "The calm cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(sensevoice, "available", return_value=True), patch.object(
        sensevoice, "ensure_model_downloaded"
    ), patch.object(
        sensevoice, "transcribe_clip", side_effect=_fake_sv
    ):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"fake webm bytes"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "finished"
    body = status["result"]
    assert body["engine"] == "sensevoice"
    assert body["transcription"] == "The calm cat."
    assert body["language_note"] is None
    assert body["score"] == 100


def test_route_hears_kana_in_the_book_script(app, app_context, client, japanese):
    """
    Regression from a real Japanese take.  The sentence writes the
    onomatopoeia わくわく in hiragana; SenseVoice answered ワクワク.  The
    word is the one that was said (100%), and the panel's "heard" line
    now spells it the way the sentence above it does, instead of looking
    like a mis-transcription.
    """
    if not shadowing.is_japanese_language(japanese):
        pytest.skip("the test database's Japanese language is unavailable")

    heard = "2ダスティンは冬休みでワクワクしています"

    def _fake_sv(audio_path, lang_code):
        assert lang_code == "ja"
        return heard, 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(sensevoice, "available", return_value=True), patch.object(
        sensevoice, "ensure_model_downloaded"
    ), patch.object(
        sensevoice, "transcribe_clip", side_effect=_fake_sv
    ):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(japanese.id),
                "tokens": json.dumps(
                    ["2", "ダスティン", "は", "冬休み", "で", "わくわく", "し", "て", "います"]
                ),
                "full_text": "2ダスティンは冬休みでわくわくしています",
                "audio": (io.BytesIO(b"fake webm bytes"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "finished"
    body = status["result"]
    assert body["engine"] == "sensevoice"
    # The transcription itself stays what the engine returned ...
    assert body["transcription"] == heard
    # ... but every word the panel shows is in the book's script.  (The
    # parser cuts the transcription's しています into four morphemes
    # where the book's span is one; the drift absorption still scores it
    # as the one word it is.)
    assert [t["text"] for t in body["transcription_tokens"]] == [
        "2",
        "ダスティン",
        "は",
        "冬休み",
        "で",
        "わくわく",
        "し",
        "て",
        "い",
        "ます",
    ]
    # The display restore must not move the score.
    assert body["statuses"] == [2, 2, 2, 2, 2, 2, 2, 2, 2]
    assert body["score"] == 100


def test_route_chinese_matches_across_simplified_traditional(app, app_context, client):
    """
    SenseVoice emits simplified Han even for Cantonese; the diff folds
    both sides so 天氣/天气 (and 係/系) compare equal.
    """
    try:
        from opencc import OpenCC  # noqa: F401
    except ImportError:
        pytest.skip("opencc not installed")

    class _ZhParser:
        def get_lowercase(self, text):
            return text.lower()

    class _ZhLanguage:
        parser_type = "spacedel"
        tts_lang = "zh-HK"
        spoken_tokens = ["今天", "天氣", "好好"]

        def __init__(self):
            self._parser = _ZhParser()

        @property
        def parser(self):
            return self._parser

        def get_parsed_tokens(self, _text):
            return [
                type("T", (), {"token": t, "is_word": True})()
                for t in self.spoken_tokens
            ]

    lang = _ZhLanguage()
    res = shadowing.compare_tokens(["今天", "天氣", "好好"], "今天天气好好", lang)
    assert res["statuses"] == [2, 2, 2]
    assert res["score"] == 100


def test_cantonese_traditional_matches_sensevoice_simplified():
    """
    Regression from a real Cantonese take.  The sentence 呢個係阿樂佢嘅...
    is traditional; SenseVoice answers in simplified with its own token
    boundaries (呢个 系阿乐 ... 剪了).  With the t2s fold plus the
    multi-token drift runs, everything actually heard matches -- 剪
    verbatim inside the glued 剪了 included; only the garbled 頭髮好長
    (heard as 道法口层) and the unheard 喇 stay misses.
    """
    try:
        from opencc import OpenCC  # noqa: F401
    except ImportError:
        pytest.skip("opencc not installed")

    lang = _FakeLanguage(
        ["呢个", "系阿乐", "佢", "嘅", "道法口层", "要", "剪了"],
        parser_type="lute_cantonese",
    )
    lang.tts_lang = "zh-HK"
    res = shadowing.compare_tokens(
        ["呢", "個", "係", "阿樂", "佢", "嘅", "頭髮", "好長", "要", "剪", "喇"],
        "呢个系阿乐佢嘅道法口层要剪了",
        lang,
    )
    assert res["statuses"] == [2, 2, 2, 2, 2, 2, 0, 0, 2, 2, 0]
    assert res["spoken_for_fuzzy"] == {}
    assert res["score"] == 73


def test_match_book_script_converts_simplified_heard_to_traditional():
    """
    SenseVoice answers 个个 都 唔 一样 for the traditional sentence
    個個 都 唔 一樣; the "heard" panel shows the transcription verbatim,
    so it is restored to the book's script (scoring is unaffected -- the
    diff keys fold to simplified either way).
    """
    try:
        from opencc import OpenCC  # noqa: F401
    except ImportError:
        pytest.skip("opencc not installed")

    lang = _FakeLanguage([], readings={})
    lang.tts_lang = "zh-HK"
    original = ["個個", "都", "唔", "一樣"]
    out = shadowing._match_book_script("个个都唔一样", original, lang)
    assert out == "個個都唔一樣"


def test_match_book_script_leaves_simplified_book_text_alone():
    "A simplified book sentence already matches the engines' output."
    try:
        from opencc import OpenCC  # noqa: F401
    except ImportError:
        pytest.skip("opencc not installed")

    lang = _FakeLanguage([], readings={})
    lang.tts_lang = "zh-HK"
    out = shadowing._match_book_script("个个都唔一样", ["个个", "都", "唔", "一样"], lang)
    assert out == "个个都唔一样"


def test_match_book_script_noop_for_non_chinese():
    "English (etc.) transcriptions are never touched."
    lang = _FakeLanguage(["the", "cat"])
    out = shadowing._match_book_script("the cat", ["The", "cat"], lang)
    assert out == "the cat"


def test_match_book_script_tolerates_missing_opencc():
    "Without opencc the transcription passes through unchanged."
    lang = _FakeLanguage([], readings={})
    lang.tts_lang = "zh-HK"
    with patch.object(shadowing, "_simplified_converter", return_value=None):
        out = shadowing._match_book_script("个个", ["個個"], lang)
    assert out == "个个"


# ---------------------------------------------------------------------
# Kana script restore (display only)
# ---------------------------------------------------------------------


def test_is_kana_only():
    "Mixed kanji/kana and non-kana tokens are never rewritten."
    assert shadowing._is_kana_only("わくわく")
    assert shadowing._is_kana_only("ワクワク")
    assert shadowing._is_kana_only("コーヒー")
    assert not shadowing._is_kana_only("冬休み")
    assert not shadowing._is_kana_only("2")
    assert not shadowing._is_kana_only("")


def test_kana_script_restore_follows_a_hiragana_book():
    "The 'heard' word is spelled the way the sentence spells it."
    lang = _FakeLanguage([], parser_type="japanese")
    restore = shadowing._kana_script_restorer(["わくわく", "し", "て"], lang)
    assert restore("ワクワク") == "わくわく"
    assert restore("シ") == "し"


def test_kana_script_restore_follows_a_katakana_book():
    "A katakana loanword the book spells in katakana stays katakana."
    lang = _FakeLanguage([], parser_type="japanese")
    restore = shadowing._kana_script_restorer(["コーヒー", "を"], lang)
    assert restore("こーひー") == "コーヒー"


def test_kana_script_restore_leaves_unrelated_words_alone():
    "A word the sentence doesn't have keeps the script the engine used."
    lang = _FakeLanguage([], parser_type="japanese")
    restore = shadowing._kana_script_restorer(["わくわく"], lang)
    assert restore("ドキドキ") == "ドキドキ"


def test_kana_script_restore_ignores_kanji_okurigana():
    "Rewriting must not flip a kanji word's okurigana (冬休み -> 冬休ミ)."
    lang = _FakeLanguage([], parser_type="japanese")
    restore = shadowing._kana_script_restorer(["冬休み", "2"], lang)
    assert restore("冬休み") == "冬休み"
    assert restore("2") == "2"


def test_kana_script_restore_noop_for_non_japanese():
    lang = _FakeLanguage([], readings={})
    restore = shadowing._kana_script_restorer(["わくわく"], lang)
    assert restore("ワクワク") == "ワクワク"


# ---------------------------------------------------------------------
# Furigana readings route
# ---------------------------------------------------------------------


def test_readings_route_returns_parallel_tokens(app, client, english):
    resp = client.post(
        "/read/shadowing/readings",
        json={"language_id": english.id, "tokens": ["The", "calm", "cat"]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["tokens"] == [
        {"text": "The", "reading": None},
        {"text": "calm", "reading": None},
        {"text": "cat", "reading": None},
    ]


def test_readings_route_rejects_bad_tokens(app, client, english):
    resp = client.post(
        "/read/shadowing/readings",
        json={"language_id": english.id, "tokens": "nope"},
    )
    assert resp.status_code == 400
    assert "tokens" in resp.get_json()["error"]


def test_readings_route_requires_language(app, client):
    resp = client.post("/read/shadowing/readings", json={"tokens": ["a"]})
    assert resp.status_code == 400
    assert "language" in resp.get_json()["error"]


def test_readings_route_rejects_bad_full_text(app, client, english):
    resp = client.post(
        "/read/shadowing/readings",
        json={"language_id": english.id, "tokens": ["a"], "full_text": 42},
    )
    assert resp.status_code == 400
    assert "tokens" in resp.get_json()["error"]


def test_readings_route_japanese_reads_in_context(app, client, japanese):
    """
    The sentence rides along with its tokens, so the parser reads it as
    a whole: 一つ comes back as ひとつ, not the isolated 一's いち.
    (Tokens are cut the way both backends would render the span.)
    """
    from lute.settings.current import current_settings

    current_settings()["japanese_reading"] = "hiragana"
    tokens = ["広い", "宇宙", "の", "数", "ある", "一つ"]
    resp = client.post(
        "/read/shadowing/readings",
        json={
            "language_id": japanese.id,
            "tokens": tokens,
            "full_text": "広い宇宙の数ある一つ",
        },
    )
    assert resp.status_code == 200
    out = resp.get_json()["tokens"]
    assert [t["text"] for t in out] == tokens
    by_text = {t["text"]: t["reading"] for t in out}
    assert by_text["一つ"] == "ひとつ"


def test_route_unknown_model_falls_back_to_default(app, app_context, client, english):
    sizes = []

    def _fake_clip(audio_path, lang_code, model_size="small"):
        sizes.append(model_size)
        return "The calm cat.", 6.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "model": "giant",
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "finished"
    assert sizes == ["small"]


def test_route_no_speech_fails_the_task(app, app_context, client, english):
    tempdir = app.env_config.temppath

    def _fake_clip(audio_path, lang_code, model_size="small"):
        return "", 2.0

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_fake_clip):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "error"
    assert "no speech" in status["error"]
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("shadowing_")]
    assert leftovers == []


def test_route_transcription_error_fails_the_task_and_cleans_temp(
    app, app_context, client, english
):
    tempdir = app.env_config.temppath

    def _boom(audio_path, lang_code, model_size="small"):
        raise RuntimeError("model exploded")

    with patch.object(
        shadowing, "whisper_status", return_value={"installed": True}
    ), patch.object(shadowing, "transcribe_clip", side_effect=_boom):
        resp = client.post(
            "/read/shadowing/transcribe",
            data={
                "language_id": str(english.id),
                "tokens": json.dumps(["The", "calm", "cat"]),
                "audio": (io.BytesIO(b"x"), "clip.webm"),
            },
            content_type="multipart/form-data",
        )

        assert resp.status_code == 200
        status = _wait_for_task(resp.get_json()["task_id"])

    assert status["state"] == "error"
    assert "model exploded" in status["error"]
    leftovers = [f for f in os.listdir(tempdir) if f.startswith("shadowing_")]
    assert leftovers == []


# ---------------------------------------------------------------------
# Attempt persistence, word marking and availability
# (real db: these run against empty_db with the demo languages)
# ---------------------------------------------------------------------


def test_persist_attempt_stores_the_take(empty_db, spanish):
    "A finished take lands in shadowattempts with its verdicts."
    shadowing.persist_attempt(
        spanish.id,
        87,
        source="review",
        sentence="Tengo un gato.",
        duration=3.2,
        tokens_per_min=42.0,
        engine="sensevoice",
        tokens_statuses=[
            {"text": "Tengo", "status": 2},
            {"text": "un", "status": 0},
            {"text": "gato", "status": 1},
        ],
    )

    from lute.models.shadowing import ShadowAttempt

    row = db.session.query(ShadowAttempt).one()
    assert row.language_id == spanish.id
    assert row.source == "review"
    assert row.score == 87
    assert row.engine == "sensevoice"
    stored = json.loads(row.tokens)
    assert [t["status"] for t in stored] == [2, 0, 1]


def test_persist_attempt_rejects_a_bogus_source(empty_db, spanish):
    "An unknown source falls back to 'read' rather than failing."
    shadowing.persist_attempt(spanish.id, 50, source="somewhere-else")

    from lute.models.shadowing import ShadowAttempt

    row = db.session.query(ShadowAttempt).one()
    assert row.source == "read"


def test_mark_word_creates_a_learning_term(empty_db, spanish):
    "A stumble on a word nobody has seen creates it as learning (1)."
    res = shadowing.mark_word_for_review(db.session, spanish, "perro")

    assert res["outcome"] == "created"
    from lute.models.term import Term

    term = db.session.query(Term).filter_by(text_lc="perro").one()
    assert term.status == 1


def test_mark_word_promotes_an_unknown_term(empty_db, spanish):
    "An unknown (0) term is promoted to learning; 1-5 words are left alone."
    terms = add_terms(spanish, ["gato", "perro"])
    terms[0].status = 0
    terms[1].status = 4
    db.session.add_all(terms)
    db.session.commit()

    assert shadowing.mark_word_for_review(db.session, spanish, "gato")["outcome"] == (
        "promoted"
    )
    assert shadowing.mark_word_for_review(db.session, spanish, "perro")["outcome"] == (
        "learning"
    )
    db.session.refresh(terms[0])
    db.session.refresh(terms[1])
    assert terms[0].status == 1
    assert terms[1].status == 4


def test_mark_word_never_demotes_known_words(empty_db, spanish):
    "Well-known and ignored words are reported, not rewritten."
    terms = add_terms(spanish, ["casa", "mesa"])
    terms[0].status = 99
    terms[1].status = 98
    db.session.add_all(terms)
    db.session.commit()

    res = shadowing.mark_word_for_review(db.session, spanish, "casa")
    assert res["outcome"] == "known"
    db.session.refresh(terms[0])
    assert terms[0].status == 99


def test_mark_word_ignores_junk(empty_db, spanish):
    "Punctuation-only or empty clicks are reported as invalid."
    res = shadowing.mark_word_for_review(db.session, spanish, "。")
    assert res["outcome"] == "invalid"


def test_mark_word_endpoint(app, client, empty_db, spanish):
    "The route answers with the outcome payload."
    resp = client.post(
        "/read/shadowing/mark_unknown",
        json={"language_id": spanish.id, "text": "perro"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "created"


def test_tokenize_for_diff_uses_the_language_parser(empty_db, spanish):
    "The review-side tokens come out in the same space the diff scores."
    toks = shadowing.tokenize_for_diff("Tengo un gato. El gato es negro.", spanish)
    assert "gato" in toks
    # Punctuation never becomes a token of its own.
    assert all(t not in (".", ",") for t in toks)


def test_transcription_available_needs_sensevoice_or_whisper(
    empty_db, spanish, monkeypatch
):
    "SenseVoice-first: available SenseVoice says yes; else whisper decides."
    monkeypatch.setattr(
        sensevoice,
        "lang_code_for",
        lambda lang: "es" if lang is spanish else None,
    )
    monkeypatch.setattr(sensevoice, "available", lambda: True)
    assert shadowing.transcription_available(spanish) is True

    monkeypatch.setattr(sensevoice, "available", lambda: False)
    monkeypatch.setattr(shadowing, "whisper_status", lambda: {"installed": True})
    assert shadowing.transcription_available(spanish) is True

    monkeypatch.setattr(shadowing, "whisper_status", lambda: {"installed": False})
    assert shadowing.transcription_available(spanish) is False
