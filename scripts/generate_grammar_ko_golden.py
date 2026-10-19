"""Verify and write the Korean grammar golden set.

The twelve hand-written Korean rules are the basic constructions every
beginner meets first, so their examples are written here rather than mined
from the curated rows: the corpus sentences that happen to fire on them are
long, often quoted from novels, and make poor illustrations of a beginner
point (〜기 전에 had exactly one candidate, a definition of slang).

What the script guarantees is that each sentence is recognised by the rule
it is filed under: the same matcher the reading panel uses runs over the
sentence, and a sentence that does not fire is reported instead of written.
``tests/unit/test_grammar_ko_golden.py`` keeps that invariant, so a rule
whose match spec is tightened later fails a test rather than silently
showing an example it no longer recognises.

Usage::

    python -m scripts.generate_grammar_ko_golden          # report
    python -m scripts.generate_grammar_ko_golden --write  # apply
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from lute.read.render import grammar_analysis_ko as ko  # noqa: E402

OUT = os.path.join(BASE, "lute", "jlpt_data", "grammar_ko_golden.json")

# {rule key: [(korean, english, chinese), ...]}
_SENTENCES = {
    "ko_go_issda": [
        ("지금 비가 오고 있어요.", "It is raining right now.", "现在正在下雨。"),
        (
            "동생은 방에서 숙제를 하고 있어요.",
            "My younger sibling is doing homework in the room.",
            "弟弟正在房间里做作业。",
        ),
    ],
    "ko_su_issda": [
        (
            "저는 한국어를 조금 말할 수 있어요.",
            "I can speak a little Korean.",
            "我会说一点韩语。",
        ),
        (
            "오늘은 시간이 없어서 갈 수 없어요.",
            "I have no time today, so I cannot go.",
            "今天没时间，去不了。",
        ),
    ],
    "ko_go_sipda": [
        ("저는 한국에 가고 싶어요.", "I want to go to Korea.", "我想去韩国。"),
        ("무엇을 먹고 싶어요?", "What do you want to eat?", "你想吃什么？"),
    ],
    "ko_ji_anhda": [
        ("오늘은 춥지 않아요.", "It is not cold today.", "今天不冷。"),
        (
            "그 사람은 술을 마시지 않습니다.",
            "That person does not drink.",
            "那个人不喝酒。",
        ),
    ],
    "ko_aeo_seo": [
        (
            "비가 와서 우산을 샀어요.",
            "It was raining, so I bought an umbrella.",
            "因为下雨，所以买了伞。",
        ),
        (
            "너무 피곤해서 일찍 잤어요.",
            "I was so tired that I went to bed early.",
            "太累了，所以早睡了。",
        ),
    ],
    "ko_eunikka": [
        (
            "비가 오니까 우산을 가져가세요.",
            "Since it is raining, take an umbrella.",
            "因为在下雨，带把伞吧。",
        ),
        (
            "내일 시험이 있으니까 오늘은 일찍 자세요.",
            "You have an exam tomorrow, so go to bed early today.",
            "明天有考试，今天早点睡吧。",
        ),
    ],
    "ko_geo_future": [
        ("저는 내일 부산에 갈 거예요.", "I am going to Busan tomorrow.", "我明天要去釜山。"),
        (
            "주말에는 집에서 쉴 거예요.",
            "I will rest at home this weekend.",
            "周末我要在家休息。",
        ),
    ],
    "ko_aeo_juda": [
        ("문을 좀 열어 주세요.", "Please open the door for me.", "请帮我开一下门。"),
        (
            "선생님이 발음을 고쳐 주셨어요.",
            "The teacher corrected my pronunciation for me.",
            "老师帮我纠正了发音。",
        ),
    ],
    "ko_gi_jeone": [
        (
            "밥을 먹기 전에 손을 씻으세요.",
            "Wash your hands before eating.",
            "吃饭前先洗手。",
        ),
        (
            "한국에 오기 전에 한국어를 배웠어요.",
            "I learned Korean before coming to Korea.",
            "来韩国之前学了韩语。",
        ),
    ],
    "ko_jung_ida": [
        ("지금 회의 중입니다.", "I am in a meeting right now.", "现在正在开会。"),
        ("책을 읽는 중이에요.", "I am in the middle of reading a book.", "正在看书。"),
    ],
    "ko_copula_polite": [
        ("저는 학생이에요.", "I am a student.", "我是学生。"),
        ("이것은 제 책입니다.", "This is my book.", "这是我的书。"),
    ],
    "ko_eumyon": [
        (
            "비가 오면 집에 있을 거예요.",
            "If it rains, I will stay at home.",
            "如果下雨，我就待在家。",
        ),
        (
            "시간이 있으면 같이 커피를 마실까요?",
            "If you have time, shall we have coffee together?",
            "如果有时间，一起喝杯咖啡好吗？",
        ),
    ],
}


def collect():
    "The golden set, the rules with no sentence, and the sentences that miss."
    rules = {
        rule["key"]: rule for rule in ko._KO_RULES
    }  # pylint: disable=protected-access
    missing = sorted(set(rules) - set(_SENTENCES))
    golden = {}
    failures = []
    for key, rows in _SENTENCES.items():
        if key not in rules:
            failures.append(f"{key}: no such rule")
            continue
        examples = []
        for korean, english, chinese in rows:
            if not ko._match_spans(  # pylint: disable=protected-access
                rules[key], ko._tokens_for(korean), korean
            ):
                failures.append(f"{key}: does not fire on {korean}")
                continue
            examples.append({"text": korean, "en": english, "zh": chinese, "ko": ""})
        if examples:
            golden[key] = {"name": rules[key]["pattern"], "examples": examples}
    return golden, missing, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the golden file")
    args = parser.parse_args()

    golden, missing, failures = collect()
    for key, entry in golden.items():
        print(f"{key}:")
        for example in entry["examples"]:
            print(f"    {example['text']}  —  {example['en']}")
    sentences = sum(len(e["examples"]) for e in golden.values())
    print(f"\n{len(golden)} rules, {sentences} sentences")
    if missing:
        print("rules with no sentence: " + ", ".join(missing))
    if failures:
        print("\n".join("FAIL " + f for f in failures))
    if args.write and not failures:
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(golden, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
