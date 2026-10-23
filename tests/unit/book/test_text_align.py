"""
Tests for transcript-to-ASR alignment.

Pure character alignment: no dependency on the recognizer, so the cases
run against hand-written cue lists.
"""

from lute.book.text_align import MIN_MATCH_RATIO, align_cues_to_reference


def _texts(aligned):
    return [c["text"] for c in aligned]


def test_text_comes_from_the_reference_and_times_from_the_cues():
    """
    The point of the whole feature: the recognizer mis-hears the names
    (prod: 張さん -> チョさん, 新郎新婦 -> 新郎新父), so the reference
    wins for text while the cues still supply the timing.
    """
    cues = [
        {"start": 91.0, "end": 96.0, "text": "李さんやチョさんのおかげで"},
        {"start": 96.0, "end": 101.0, "text": "新郎新父にあんなにお酒を飲ませるとは"},
    ]
    reference = "李さんや張さんのおかげで\n新郎新婦にあんなにお酒を飲ませるとは"

    aligned, ratio = align_cues_to_reference(cues, reference)

    assert ratio > 0.8
    assert _texts(aligned) == [
        "李さんや張さんのおかげで",
        "新郎新婦にあんなにお酒を飲ませるとは",
    ]
    # Times come from the cues, not from a made-up distribution.
    assert aligned[0]["start"] == 91.0
    assert aligned[1]["end"] == 101.0


def test_reference_punctuation_is_kept_and_defines_the_sentences():
    "Cues are cut where the reference says, not where the VAD paused."
    cues = [
        {"start": 0.0, "end": 4.0, "text": "李さん例のプロジェクト"},
        {"start": 4.0, "end": 7.0, "text": "秋には始まりそうですよ"},
    ]
    reference = "李さん、例のプロジェクト。\n秋には始まりそうですよ。"

    aligned, ratio = align_cues_to_reference(cues, reference)

    assert ratio > 0.9
    assert _texts(aligned) == [
        "李さん、例のプロジェクト。",
        "秋には始まりそうですよ。",
    ]
    assert aligned[0]["start"] == 0.0
    assert aligned[-1]["end"] == 7.0


def test_a_sentence_the_recognizer_missed_entirely_still_gets_a_time():
    """
    It has no audio of its own to point at, so its time is interpolated
    between the sentences around it -- and crucially the cursor does not
    move, so the *next* sentence is still located against the right audio
    instead of being dragged along behind a phantom.
    """
    cues = [
        {"start": 0.0, "end": 2.0, "text": "前半の文"},
        {"start": 10.0, "end": 12.0, "text": "後半の文"},
    ]
    reference = "前半の文\nXYZ\n後半の文"

    aligned, _ratio = align_cues_to_reference(cues, reference)

    assert _texts(aligned) == ["前半の文", "XYZ", "後半の文"]
    first, middle, last = aligned
    assert first["end"] <= middle["start"]
    assert middle["end"] <= last["start"]
    # The two sentences that *are* in the audio keep their real times.
    assert first["start"] == 0.0
    assert first["end"] == 2.0
    assert last["start"] == 10.0
    assert last["end"] == 12.0


def test_a_shared_word_far_ahead_does_not_pull_the_timeline_forward():
    """
    A heading shares one word with a line 20 seconds later.  Accepting
    that as a match jumps the cursor over the audio in between, and every
    later sentence lands late -- the "subtitles drift further and further
    behind" report (prod book 879).  A partial match like that must be
    treated as no match at all.
    """
    cues = [
        {"start": 0.0, "end": 2.0, "text": "最初の行です"},
        {"start": 20.0, "end": 24.0, "text": "前回のメールで確認しました"},
    ]
    reference = "最初の行です\n###メール2\n前回のメールで確認しました"

    aligned, _ratio = align_cues_to_reference(cues, reference)

    assert _texts(aligned) == [
        "最初の行です",
        "###メール2",
        "前回のメールで確認しました",
    ]
    # The heading has no audio: it belongs between the two lines, not on
    # top of the second one's "メール".
    assert aligned[1]["start"] < 20.0
    assert aligned[1]["end"] <= 20.0
    # The real sentence keeps its real time.
    assert aligned[2]["start"] == 20.0
    assert aligned[2]["end"] == 24.0


def test_a_long_unmatched_run_does_not_run_past_the_recording():
    """
    Unmatched sentences used to be given a fixed 0.12 s per character,
    which has no idea how much audio the run is standing in: enough of
    them invented more time than the recording had and pushed the last
    line past the end of the audio.  Interpolation keeps the run inside
    the interval its neighbours anchor.
    """
    filler = "\n".join(["音声には出てこない行がここにあります"] * 15)
    cues = [
        {"start": 0.0, "end": 2.0, "text": "前半の文"},
        {"start": 30.0, "end": 32.0, "text": "後半の文"},
    ]
    reference = f"前半の文\n{filler}\n後半の文"

    aligned, _ratio = align_cues_to_reference(cues, reference)

    assert aligned[0]["start"] == 0.0
    assert aligned[-1]["start"] == 30.0
    assert aligned[-1]["end"] == 32.0
    for cue in aligned:
        assert cue["end"] <= 32.0
    # Nothing inside the run is squeezed to nothing.
    for cue in aligned[1:-1]:
        assert cue["end"] > cue["start"]


def test_timeline_stays_monotonic_when_sentences_share_a_cue_character():
    "A very short sentence packed into one cue must not go backwards."
    cues = [{"start": 0.0, "end": 1.0, "text": "はい"}]
    reference = "はい\nはい"

    aligned, _ratio = align_cues_to_reference(cues, reference)

    assert len(aligned) == 2
    assert aligned[0]["start"] <= aligned[0]["end"] <= aligned[1]["start"]
    assert aligned[1]["end"] > aligned[1]["start"]


def test_whitespace_in_the_reference_is_ignored():
    "Pasted text has indentation and stray spaces; they must not dilute it."
    cues = [{"start": 0.0, "end": 3.0, "text": "中国式にしましょう"}]
    reference = "  中国 式 に し ましょう  "

    aligned, ratio = align_cues_to_reference(cues, reference)

    assert ratio == 1.0
    assert _texts(aligned) == ["中国式にしましょう"]


def test_a_long_reference_sentence_is_split_only_at_its_punctuation():
    cues = [{"start": 0.0, "end": 6.0, "text": "これは長い文ですがまだ続きます"}]
    reference = "これは長い文ですが、まだ続きます。"

    aligned, _ratio = align_cues_to_reference(cues, reference)

    # No sentence end before the final 、-less mark: one cue, not two.
    assert _texts(aligned) == ["これは長い文ですが、まだ続きます。"]


def test_unrelated_and_empty_inputs():
    cues = [{"start": 0.0, "end": 3.0, "text": "全然違う内容がここにあります"}]

    aligned, ratio = align_cues_to_reference(cues, "まったく別の文章です")
    assert ratio < MIN_MATCH_RATIO
    # It still returns a mapping -- the caller judges the ratio.
    assert aligned is None or len(aligned) == 1

    assert align_cues_to_reference([], "なにか") == (None, 0.0)
    assert align_cues_to_reference(cues, "") == (None, 0.0)
    assert align_cues_to_reference(cues, "   \n  ") == (None, 0.0)
    assert align_cues_to_reference(None, None) == (None, 0.0)


def test_cues_with_empty_text_are_skipped():
    cues = [
        {"start": 0.0, "end": 1.0, "text": ""},
        {"start": 1.0, "end": 3.0, "text": "本文です"},
        {"start": 3.0, "end": 4.0, "text": None},
    ]
    aligned, _ratio = align_cues_to_reference(cues, "本文です")

    assert _texts(aligned) == ["本文です"]
    assert aligned[0]["start"] == 1.0
