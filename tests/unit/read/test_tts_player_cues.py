"""
The TTS player reads a long sentence one clause at a time.

A long sentence spoken as one utterance is a 12-second wall of sound:
no place to loop, nothing to shadow, no break to breathe in.  The
player therefore splits long sentences into clause cues at punctuation
(ttsSplitSentenceIntoCues) and holds a short rest between a sentence's
own cues (ttsAdvance), so subtitle, loop, auto-pause and shadowing all
work on the clause.

Everything is driven against the real tts-player.js (and
lute-shadowing.js for the clause spans) by tts_player_cues_harness.js:
a stub DOM, a manual timer queue and a fake SpeechSynthesis engine whose
every utterance takes exactly 400 ms of fake time, which pins the
pacing precisely -- a clause boundary lands 720 ms after the previous
clause started (400 speech + 300 rest + 20 start delay), a sentence
boundary only 420 ms (no rest).

Skips when node isn't on PATH (CI's pytest job doesn't guarantee it).
"""

import json
import os
import shutil
import subprocess

import pytest

import lute

NODE = shutil.which("node")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_HARNESS = os.path.join(_HERE, "tts_player_cues_harness.js")
_STATIC_JS = os.path.join(os.path.dirname(lute.__file__), "static", "js")
_TTS_JS = os.path.join(_STATIC_JS, "tts-player.js")
_SHADOWING_JS = os.path.join(_STATIC_JS, "lute-shadowing.js")

# 故乡, 鲁迅 -- the sentence that motivated this: 68 characters, read
# straight through it is ~12 s with nowhere to stop.
LONG_CJK = (
    "时候既然是深冬；渐近故乡时，天气又阴晦了，冷风吹进船舱中，呜呜的响，"
    "从蓬隙向外一望，苍黄的天底下，远近横著几个萧索的荒村，没有一些活气。"
)
SHORT_CJK = "我的心禁不住悲凉起来了。"

EXPECTED_CLAUSES = [
    "时候既然是深冬；渐近故乡时，",
    "天气又阴晦了，冷风吹进船舱中，",
    "呜呜的响，从蓬隙向外一望，",
    "苍黄的天底下，远近横著几个萧索的荒村，",
    "没有一些活气。",
]


def _run(scenario):
    "Feed a scenario to the node harness and return its state snapshots."
    if NODE is None:
        pytest.skip("node is not on PATH")
    if not os.path.exists(_HARNESS):
        pytest.skip("tts-player cues harness not found")
    # Bytes in and out with an explicit encoding: the scenario carries
    # CJK, and CI locales are not guaranteed to be UTF-8.
    proc = subprocess.run(
        [NODE, _HARNESS, _TTS_JS, _SHADOWING_JS],
        input=json.dumps(scenario).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8")
    return json.loads(proc.stdout.decode("utf-8"))["results"]


def _two_sentence_scenario(steps):
    return {
        "sentences": [{"cjk": LONG_CJK}, {"cjk": SHORT_CJK}],
        "steps": steps,
    }


def test_a_long_cjk_sentence_becomes_clause_cues():
    "The long sentence is cut at its punctuation; the short one stays whole."
    results = _run(
        _two_sentence_scenario([{"build": True}, {"state": True}])
    )
    cues = results[-1]["state"]["cues"]
    assert [c["text"] for c in cues] == EXPECTED_CLAUSES + [SHORT_CJK]
    # Every clause of the long sentence but the last is followed by a
    # rest; a sentence's last cue and the whole short sentence are not.
    assert [c["pauseAfter"] for c in cues] == [0.3, 0.3, 0.3, 0.3, 0, 0]
    # The clause cues carry their own live spans (for shadowing); a
    # whole-sentence cue needs none.
    assert [c["hasSpanEls"] for c in cues] == [
        True,
        True,
        True,
        True,
        True,
        False,
    ]
    # The spans are the clause's own, punctuation spans included (the
    # counts are the clause lengths, minus the 🔊 button no splitter
    # ever sees).
    assert [c["spanCount"] for c in cues] == [14, 15, 13, 19, 7, 0]


def test_playback_holds_a_rest_between_clauses_not_between_sentences():
    "The rest is audible inside the sentence, and absent across sentences."
    results = _run(
        _two_sentence_scenario(
            [{"play": True}, {"flush_all": True}, {"state": True}]
        )
    )
    state = results[-1]["state"]
    assert [s["text"] for s in state["spoken"]] == EXPECTED_CLAUSES + [SHORT_CJK]
    # 400 ms fake speech: clause -> clause = 400 + 300 rest + 20 start
    # delay = 720; the sentence boundary has no rest: 400 + 20 = 420.
    assert [s["at"] for s in state["spoken"]] == [20, 740, 1460, 2180, 2900, 3320]


def test_cue_events_fire_per_clause_with_the_clause_spans():
    "Shadowing's hooks see one event per clause, spans handed over."
    results = _run(
        _two_sentence_scenario(
            [{"play": True}, {"flush_all": True}, {"state": True}]
        )
    )
    events = results[-1]["state"]["events"]
    changed = [e for e in events if e["type"] == "lute:cue-changed"]
    ended = [e for e in events if e["type"] == "lute:cue-ended"]
    assert len(ended) == 6  # one per cue, clause or not
    assert [(e["sentIdx"], e["hasSpanEls"]) for e in changed] == [
        (0, True),
        (0, True),
        (0, True),
        (0, True),
        (0, True),
        (1, False),
        # ... and the final deactivate when the stream ends.
        (-1, False),
    ]


def test_auto_pause_parks_on_the_clause():
    "Auto-pause stops after a clause and replays that clause on resume."
    results = _run(
        {
            "sentences": [{"cjk": LONG_CJK}],
            "steps": [
                {"autopause": True},
                {"play": True},
                {"flush_all": True},
                {"state": True},
                {"play": True},
                {"flush_all": True},
                {"state": True},
            ],
        }
    )
    first = results[0]["state"]
    assert [s["text"] for s in first["spoken"]] == [EXPECTED_CLAUSES[0]]
    assert first["tts"]["playing"] is False
    assert first["tts"]["paused"] is True
    assert first["tts"]["cueIndex"] == 0
    assert first["tts"]["virtualTime"] == 0  # rewound to the clause start
    # Resuming replays the SAME clause, then parks again on it.
    second = results[-1]["state"]
    assert [s["text"] for s in second["spoken"]] == [
        EXPECTED_CLAUSES[0],
        EXPECTED_CLAUSES[0],
    ]
    assert second["tts"]["paused"] is True
    assert second["tts"]["cueIndex"] == 0


def test_stop_during_the_clause_rest_kills_the_pending_cue():
    "A stop while the player is resting between clauses silences playback."
    results = _run(
        {
            "sentences": [{"cjk": LONG_CJK}],
            "steps": [
                {"play": True},
                {"flush_one": True},  # the 20 ms start delay -> clause 1 speaks
                {"flush_one": True},  # its 400 ms -> onend, the rest timer queues
                {"stop": True},  # ... before the rest timer fires
                {"flush_all": True},
                {"state": True},
            ],
        }
    )
    # The scenario stops after the third flush -- the rest timer is
    # still queued -- so clause 2 must never speak.
    state = results[-1]["state"]
    assert [s["text"] for s in state["spoken"]] == [EXPECTED_CLAUSES[0]]
    assert state["tts"]["playing"] is False
    assert state["tts"]["cueIndex"] == -1


def test_a_latin_sentence_splits_at_punctuation_with_a_longer_minimum():
    "Latin needs 140 chars to split and keeps fragments >= 40 chars."
    results = _run(
        {
            "sentences": [
                {
                    "latin": (
                        "The first part of this sentence runs on for quite a "
                        "while, and then it continues here; after the semicolon "
                        "there is more text that goes on, until the very end."
                    )
                }
            ],
            "steps": [{"build": True}, {"play": True}, {"flush_all": True}, {"state": True}],
        }
    )
    state = results[-1]["state"]
    # The semicolon at 23 running chars is below the 40-char minimum, so
    # it does not split; the commas at >= 40 do.
    assert [c["text"] for c in state["cues"]] == [
        "The first part of this sentence runs on for quite a while,",
        "and then it continues here; after the semicolon there is more text that goes on,",
        "until the very end.",
    ]
    assert [s["at"] for s in state["spoken"]] == [20, 740, 1460]


def test_shadowing_practises_the_clause_the_cue_covers():
    "A clause cue hands shadowing the clause's own spans, not the sentence."
    results = _run(
        {
            "sentences": [{"cjk": LONG_CJK}],
            "steps": [
                {"build": True},
                {"shadow_apply_cue": 1},
                {"state": True},
                {"shadow_apply_whole": 0},
                {"state": True},
            ],
        }
    )
    clause = results[0]["state"]["shadowing"]
    assert clause["texts"] == [
        "天",
        "气",
        "又",
        "阴",
        "晦",
        "了",
        "冷",
        "风",
        "吹",
        "进",
        "船",
        "舱",
        "中",
    ]
    assert clause["fullText"] == "天气又阴晦了，冷风吹进船舱中，"
    assert clause["langId"] == "1"
    assert clause["elConnected"] is True
    # Without spans (a whole-sentence cue, the pre-clause shape), the
    # whole sentence is the unit.
    whole = results[-1]["state"]["shadowing"]
    assert whole["spanCount"] == 59
    assert whole["fullText"] == LONG_CJK
    assert whole["elConnected"] is True
