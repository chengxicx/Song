"""Build the Thai grammar golden set from the project's own corpus.

The Thai corpus (``grammar_th.json``) was written for this project and its
examples already carry a romanisation, an English gloss and a Chinese one
in a single line: ``"{thai} · {roman} — {english} ／ {中文}"``.  The
hand-written rules cover the basic constructions, so their golden
sentences are picked from those rows rather than written again -- one
authoritative source per sentence.

Selection, per rule:

  * the Thai part of the line must actually fire on the rule (the same
    matcher the panel uses);
  * a *sentence* is preferred over a bare phrase: the corpus mixes both
    ("จังหวัดนครพนม" is a noun phrase), and a golden example is there to
    show the construction in use;
  * rows whose name shares a literal with the rule rank first, so an
    example that fires by accident (〜ไม่ in an unrelated negative) does
    not win;
  * at most two per rule, translated ones first, no sentence reused.

Two exceptions are pinned by sentence text (``_PINNED``).  The ranking is
greedy and order-sensitive, so a row that gains examples near the top of
the corpus can outrank a better one further down -- and the length proxy
below mistakes a short but complete sentence (ผมชอบเขา) for a phrase.  A
pin keeps the curated sentence and is verified like any other.

Three rules have no firing example at all in the corpus -- ``th_both``,
``th_almost`` and ``th_formal_concessive`` -- so their sentences are
written here.

Usage::

    python -m scripts.generate_grammar_th_golden          # report
    python -m scripts.generate_grammar_th_golden --write  # apply
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from lute.read.render import grammar_analysis_th as th  # noqa: E402
from lute.read.render.grammar_analysis_matcher import match_rule  # noqa: E402

OUT = os.path.join(BASE, "lute", "jlpt_data", "grammar_th_golden.json")
MAX_PER_RULE = 2

# Rules the corpus cannot illustrate: either no sentence fires at all
# (th_can) or every firing sentence shows a *different* reading of the same
# word -- ที่ as the preposition "at", น่า as a bare emphatic, ด้วย as
# "with".  Written here, and verified by the same matcher on the way out.
_AUTHORED = {
    "th_both": [
        {
            "text": "เขาทั้งเก่งและขยัน",
            "en": "He is both capable and hard-working.",
            "zh": "他既能干又勤奋。",
            "ko": "그는 능숙하기도 하고 부지런하기도 하다.",
        }
    ],
    "th_almost": [
        {
            "text": "ฉันเกือบตกรถ",
            "en": "I almost missed the bus.",
            "zh": "我差点没赶上车。",
            "ko": "나는 하마터면 차를 놓칠 뻔했다.",
        }
    ],
    "th_formal_concessive": [
        {
            "text": "ฝนตกหนัก อย่างไรก็ตาม เรายังไปกัน",
            "en": "It rained hard; however, we still went.",
            "zh": "雨下得很大，然而我们还是去了。",
            "ko": "비가 많이 왔지만, 그럼에도 우리는 갔다.",
        }
    ],
    "th_thi": [
        {
            "text": "คนที่ยืนอยู่ตรงนั้นเป็นครูของฉัน",
            "en": "The person standing over there is my teacher.",
            "zh": "站在那里的那个人是我的老师。",
            "ko": "저기 서 있는 사람이 내 선생님이다.",
        },
        {
            "text": "นี่คือหนังสือที่ฉันซื้อเมื่อวาน",
            "en": "This is the book I bought yesterday.",
            "zh": "这是我昨天买的书。",
            "ko": "이것은 내가 어제 산 책이다.",
        },
    ],
    "th_na_worth": [
        {
            "text": "หนังเรื่องนี้น่าสนใจมาก",
            "en": "This film is very interesting.",
            "zh": "这部电影很有意思。",
            "ko": "이 영화는 아주 흥미롭다.",
        },
        {
            "text": "อาหารร้านนี้อร่อยน่ากิน",
            "en": "The food at this shop looks delicious.",
            "zh": "这家店的食物看起来很好吃。",
            "ko": "이 가게 음식은 맛있어 보인다.",
        },
    ],
    "th_also": [
        {
            "text": "ฉันก็ไปด้วย",
            "en": "I am going too.",
            "zh": "我也去。",
            "ko": "나도 간다.",
        },
        {
            "text": "เขาก็เห็นด้วยเหมือนกัน",
            "en": "He agrees as well.",
            "zh": "他也同意。",
            "ko": "그도 동의한다.",
        },
    ],
    "th_can": [
        {
            "text": "เขาสามารถว่ายน้ำได้",
            "en": "He can swim.",
            "zh": "他会游泳。",
            "ko": "그는 수영할 수 있다.",
        },
        {
            "text": "คุณสามารถจ่ายด้วยบัตรได้",
            "en": "You can pay by card.",
            "zh": "你可以用卡支付。",
            "ko": "카드로 결제할 수 있다.",
        },
    ],
}


# Sentences a rule must keep, given as the corpus text.  Both rules below
# are illustrated by the rows that teach them, but those sentences were
# being displaced: the corpus says "ผมชอบเขา" and "คุณทำอะไรอยู่", which are
# short enough for the length proxy to read as phrases, so any later row
# that gained a longer example sharing one literal won the slot.  Pinning
# states the choice once; the matcher still has to agree.
_PINNED = {
    "th_pronouns": ["ผมชอบเขา", "เขาชอบผม"],
    "th_questions": ["คุณทำอะไรอยู่", "คุณซื้ออะไรมา"],
}


def _parse(line):
    "One corpus example line -> {thai, roman, en, zh}."
    text, _, zh = line.partition(th._GLOSS_SEP)  # pylint: disable=protected-access
    thai, _, rest = text.partition("·")
    roman, _, en = rest.partition("—")
    return {
        "thai": thai.strip(),
        "roman": roman.strip(),
        "en": en.strip(),
        "zh": zh.strip(),
    }


def _rule_literals(rule):
    "The Thai literals a rule looks for, from its match spec."
    match = rule.get("match") or {}
    specs = match.get("any_of") if isinstance(match.get("any_of"), list) else [match]
    literals = set()
    for spec in specs:
        for cond in spec.get("seq") or []:
            literals |= set(cond.get("surface_in") or ())
        for side in ("left", "right"):
            for cond in spec.get(side) or []:
                literals |= set(cond.get("surface_in") or ())
    return {lit for lit in literals if lit}


_THAI_RUN = __import__("re").compile(r"[\u0e00-\u0e7f]+")


def _row_literals(item):
    """
    The Thai literals a corpus row is *about*, from its title.

    Corpus rows carry no match spec -- their title is the pattern
    ("คุณ + ชื่อ", "ทั้ง ... และ") -- so the Thai runs of the title are the
    only statement of what the row teaches.  This is what tells a real
    example of ถูก (passive) from a sentence where ถูก means "cheap".
    """
    return set(_THAI_RUN.findall(str(item.get("name") or "")))


def _is_sentence(parsed):
    """
    Whether the example is a sentence rather than a bare noun phrase.

    The corpus mixes both.  A phrase has no verb, so it cannot show what a
    construction does; length alone is a decent proxy, and the corpus'
    sentences all run past twelve characters.
    """
    return len(parsed["thai"]) >= 12


def _golden_row(parsed):
    "One parsed corpus line as a golden example row."
    return {
        "text": parsed["thai"],
        "en": parsed["en"],
        "zh": parsed["zh"],
        "ko": "",
    }


def collect():
    "The golden set, the rules with no example, and the sentences that miss."
    corpus = th._corpus()  # pylint: disable=protected-access
    golden = {}
    used = set()
    failures = []
    pinned = {text for texts in _PINNED.values() for text in texts}
    by_text = {}
    for item in corpus:
        for line in item.get("examples") or ():
            parsed = _parse(line)
            if parsed["thai"]:
                by_text.setdefault(parsed["thai"], parsed)
    for rule in th._TH_RULES:
        if rule["key"] in _AUTHORED:
            # Authored rows are checked exactly like derived ones: a sentence
            # written by hand is still wrong until the matcher says otherwise.
            rows = [
                row
                for row in _AUTHORED[rule["key"]]
                if match_rule(rule, th._tokens_for(row["text"]), row["text"])
            ]
            for row in _AUTHORED[rule["key"]]:
                if row not in rows:
                    failures.append(f"{rule['key']}: does not fire on {row['text']}")
            if rows:
                golden[rule["key"]] = {"name": rule["name"], "examples": rows}
                used.update(row["text"] for row in rows)
            continue
        chosen = []
        for text in _PINNED.get(rule["key"], ()):
            parsed = by_text.get(text)
            if parsed is None:
                failures.append(f"{rule['key']}: pinned text not in the corpus: {text}")
            elif text in used:
                failures.append(f"{rule['key']}: pinned text taken earlier: {text}")
            elif not match_rule(rule, th._tokens_for(text), text):
                failures.append(f"{rule['key']}: does not fire on pinned {text}")
            else:
                chosen.append(_golden_row(parsed))
                used.add(text)
        literals = _rule_literals(rule)
        candidates = []
        for order, item in enumerate(corpus):
            shared = bool(literals & _row_literals(item))
            for line in item.get("examples") or ():
                parsed = _parse(line)
                thai = parsed["thai"]
                if not thai or thai in used or thai in pinned:
                    continue
                if not match_rule(rule, th._tokens_for(thai), thai):
                    continue
                score = (
                    0 if _is_sentence(parsed) else 1,
                    0 if shared else 1,
                    order,
                    -sum(1 for f in ("en", "zh") if parsed[f]),
                    len(thai),
                )
                candidates.append((score, parsed))
        candidates.sort(key=lambda pair: pair[0])
        # Only a sentence from a row about the same construction is trusted:
        # the rules are broad enough that an unrelated sentence fires by
        # accident (ถูก "cheap" for the passive rule, ด้วย "with" for the
        # "also" rule), and a golden example that teaches the wrong reading
        # is worse than none.
        candidates = [pair for pair in candidates if pair[0][1] == 0]
        for _score, parsed in candidates[: MAX_PER_RULE - len(chosen)]:
            chosen.append(_golden_row(parsed))
            used.add(parsed["thai"])
        if chosen:
            golden[rule["key"]] = {"name": rule["name"], "examples": chosen}
        elif rule["key"] in _AUTHORED:
            golden[rule["key"]] = {
                "name": rule["name"],
                "examples": _AUTHORED[rule["key"]],
            }
    missing = [rule["key"] for rule in th._TH_RULES if rule["key"] not in golden]
    return golden, missing, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the golden file")
    args = parser.parse_args()

    golden, missing, failures = collect()
    for key, entry in golden.items():
        print(f"{key}:")
        for example in entry["examples"]:
            tags = "".join("+" if example[f] else "-" for f in ("en", "zh"))
            print(f"    [{tags}] {example['text']}")
    sentences = sum(len(e["examples"]) for e in golden.values())
    print(f"\n{len(golden)} rules, {sentences} sentences")
    if missing:
        print("no example at all (write by hand): " + ", ".join(missing))
    if failures:
        print("\n".join("FAIL " + f for f in failures))
    if args.write and not failures:
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(golden, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
