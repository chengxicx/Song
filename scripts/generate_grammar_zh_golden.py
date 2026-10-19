"""Verify and write the Mandarin grammar golden set.

The Mandarin engine is a hand-written rule set with no corpus behind it --
there is no ``grammar_zh.json`` -- so its golden sentences are written here
rather than derived.  Every one of the 39 rules gets two, each carrying an
English and a Korean gloss so the library page can show a translation on
every display language.

What the script actually guarantees is that each sentence is recognised by
the rule it is filed under: the same matcher the reading panel uses runs
over the sentence, and a sentence that does not fire is reported instead of
written.  That is the invariant ``tests/unit/test_grammar_zh_golden.py``
keeps, so a rule whose match spec is tightened later fails a test rather
than silently showing an example it no longer recognises.

Usage::

    python -m scripts.generate_grammar_zh_golden          # report
    python -m scripts.generate_grammar_zh_golden --write  # apply
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from lute.read.render import grammar_analysis_zh as zh  # noqa: E402
from lute.read.render.grammar_analysis_matcher import match_rule  # noqa: E402

OUT = os.path.join(BASE, "lute", "jlpt_data", "grammar_zh_golden.json")

# {rule key: [(chinese, english, korean), ...]}
_SENTENCES = {
    "zh_le": [
        ("我昨天买了一本书。", "I bought a book yesterday.", "나는 어제 책을 한 권 샀다."),
        (
            "天气冷了，该穿外套了。",
            "It has turned cold; time to put on a coat.",
            "날씨가 추워졌으니 외투를 입을 때다.",
        ),
    ],
    "zh_zhe": [
        ("门上贴着一张纸条。", "A note is stuck to the door.", "문에 쪽지가 붙어 있다."),
        ("他笑着跟我打招呼。", "He greeted me with a smile.", "그는 웃으며 나에게 인사했다."),
    ],
    "zh_guo": [
        ("我去过中国三次。", "I have been to China three times.", "나는 중국에 세 번 가 봤다."),
        (
            "这部电影我看过，不想再看了。",
            "I have seen this film; I do not want to see it again.",
            "이 영화는 봤기 때문에 다시 보고 싶지 않다.",
        ),
    ],
    "zh_bu": [
        ("我今天不去公司。", "I am not going to the office today.", "나는 오늘 회사에 가지 않는다."),
        (
            "这道题不难，你再想想。",
            "This problem is not hard; think again.",
            "이 문제는 어렵지 않으니 다시 생각해 봐.",
        ),
    ],
    "zh_mei": [
        ("我还没吃早饭。", "I have not eaten breakfast yet.", "나는 아직 아침을 먹지 않았다."),
        ("他昨天没有来上课。", "He did not come to class yesterday.", "그는 어제 수업에 오지 않았다."),
    ],
    "zh_zai": [
        ("妈妈在厨房做饭。", "Mum is cooking in the kitchen.", "엄마가 부엌에서 요리하고 계셔."),
        ("我的书在桌子上。", "My book is on the table.", "내 책은 책상 위에 있다."),
    ],
    "zh_question_words": [
        ("你在找什么？", "What are you looking for?", "무엇을 찾고 있어?"),
        ("这句话该怎么翻译？", "How should this sentence be translated?", "이 문장은 어떻게 번역해야 해?"),
    ],
    "zh_ma": [
        ("你明天有空吗？", "Are you free tomorrow?", "내일 시간 있어?"),
        ("这就是你的决定吗？", "Is this your decision?", "이것이 네 결정이야?"),
    ],
    "zh_ne": [
        ("我很好，你呢？", "I am fine, and you?", "나는 잘 지내, 너는?"),
        ("他正在睡觉呢。", "He is sleeping.", "그는 자고 있어."),
    ],
    "zh_ba_q": [
        ("我们走吧，时间不早了。", "Let us go; it is getting late.", "우리 가자, 시간이 늦었어."),
        ("快点儿吧，大家都等你呢。", "Hurry up; everyone is waiting for you.", "어서 해, 모두 기다리고 있어."),
    ],
    "zh_ye_dou": [
        ("我们也都想去。", "We all want to go too.", "우리도 모두 가고 싶어."),
        (
            "这道题太难了，谁都不会。",
            "This problem is so hard nobody can do it.",
            "이 문제는 너무 어려워서 아무도 못 한다.",
        ),
    ],
    "zh_ge": [
        ("我只有一个问题。", "I have only one question.", "나는 질문이 하나 있어."),
        ("请给我三个苹果。", "Please give me three apples.", "사과 세 개 주세요."),
    ],
    "zh_shi_de": [
        (
            "我是昨天到的上海。",
            "It was yesterday that I arrived in Shanghai.",
            "나는 어제 상하이에 도착했다.",
        ),
        (
            "这本书是朋友送给我的。",
            "This book was given to me by a friend.",
            "이 책은 친구가 나에게 준 것이다.",
        ),
    ],
    "zh_ba_sentence": [
        ("我把作业做完了。", "I have finished my homework.", "나는 숙제를 다 했다."),
        ("请把牛奶放在冰箱里。", "Please put the milk in the fridge.", "우유를 냉장고에 넣어 주세요."),
    ],
    "zh_bei_sentence": [
        ("我的手机被小偷偷了。", "My phone was stolen by a thief.", "내 휴대폰을 도둑맞았다."),
        ("这个问题终于被解决了。", "The problem was finally solved.", "그 문제는 마침내 해결되었다."),
    ],
    "zh_zhengzai": [
        (
            "他正在开会，不能接电话。",
            "He is in a meeting and cannot take the call.",
            "그는 회의 중이라 전화를 받을 수 없다.",
        ),
        ("妈妈正在做饭，我们等等吧。", "Mum is cooking; let us wait.", "엄마가 요리 중이니 기다리자."),
    ],
    "zh_cai_jiu": [
        ("他五点就起床了。", "He got up as early as five.", "그는 다섯 시에 일어났다."),
        (
            "我等到八点，他才来。",
            "I waited until eight before he came.",
            "나는 여덟 시까지 기다렸다, 그제서야 그가 왔다.",
        ),
    ],
    "zh_geng_zui": [
        ("这件衣服比那件更好看。", "This shirt looks better than that one.", "이 옷이 저것보다 더 예쁘다."),
        ("他是我最好的朋友。", "He is my best friend.", "그는 내 가장 친한 친구다."),
    ],
    "zh_bibi": [
        ("他比我高一个头。", "He is a head taller than me.", "그는 나보다 머리 하나 더 크다."),
        ("今年的冬天比去年冷。", "This winter is colder than last year's.", "올해 겨울은 작년보다 춥다."),
    ],
    "zh_yinwei_suoyi": [
        (
            "因为下雨了，所以我们没去公园。",
            "Because it rained, we did not go to the park.",
            "비가 왔기 때문에 우리는 공원에 가지 않았다.",
        ),
        (
            "他没来上课，因为他生病了。",
            "He did not come to class because he was ill.",
            "그는 아팠기 때문에 수업에 오지 않았다.",
        ),
    ],
    "zh_hai": [
        (
            "这么晚了，他还在工作。",
            "It is so late and he is still working.",
            "이렇게 늦었는데 그는 아직 일하고 있다.",
        ),
        (
            "除了英语，他还会说日语。",
            "Besides English, he can also speak Japanese.",
            "영어 외에 그는 일본어도 할 줄 안다.",
        ),
    ],
    "zh_yueyue": [
        ("雨越下越大。", "The rain is falling harder and harder.", "비가 점점 더 세게 내린다."),
        (
            "我越听越觉得你说得对。",
            "The more I listen, the more I think you are right.",
            "들을수록 네 말이 맞는 것 같아.",
        ),
    ],
    "zh_yibian_yibian": [
        ("他一边走路一边打电话。", "He talks on the phone while walking.", "그는 걸으면서 전화를 한다."),
        (
            "我喜欢一边听音乐一边做饭。",
            "I like cooking while listening to music.",
            "나는 음악을 들으면서 요리하는 것을 좋아한다.",
        ),
    ],
    "zh_ruguo": [
        (
            "如果明天不下雨，我们就去爬山。",
            "If it does not rain tomorrow, we will go climbing.",
            "내일 비가 오지 않으면 우리는 등산하러 갈 거야.",
        ),
        (
            "要是早走十分钟，就不会迟到了。",
            "If we had left ten minutes earlier, we would not have been late.",
            "십 분만 더 일찍 떠났다면 늦지 않았을 거야.",
        ),
    ],
    "zh_chule": [
        (
            "除了小王，其他人都到了。",
            "Everyone has arrived except Xiao Wang.",
            "샤오왕을 제외하고 모두 도착했다.",
        ),
        (
            "除了英语，他还学法语。",
            "Besides English, he also studies French.",
            "영어 외에 그는 프랑스어도 배운다.",
        ),
    ],
    "zh_yijing": [
        (
            "我已经吃过饭了，你们慢慢吃。",
            "I have already eaten; take your time.",
            "나는 이미 밥을 먹었으니 천천히 먹어.",
        ),
        (
            "电影已经开始了，快点进去吧。",
            "The film has already started; hurry in.",
            "영화가 이미 시작했으니 어서 들어가.",
        ),
    ],
    "zh_zhiqian_zhihou": [
        (
            "来中国以前，我一句中文都不会。",
            "Before coming to China I could not speak a word of it.",
            "중국에 오기 전에는 중국어를 한 마디도 못 했다.",
        ),
        (
            "下课以后，我们去打球吧。",
            "After class, let us go and play ball.",
            "수업이 끝난 뒤에 우리 공 치러 가자.",
        ),
    ],
    "zh_rang_jiao": [
        ("老师让我念课文。", "The teacher asked me to read the text.", "선생님이 나에게 본문을 읽게 했다."),
        ("我的帽子叫风吹跑了。", "My hat was blown away by the wind.", "내 모자는 바람에 날려갔다."),
    ],
    "zh_wulun": [
        (
            "不管多忙，他都要锻炼身体。",
            "However busy he is, he always exercises.",
            "아무리 바빠도 그는 늘 운동을 한다.",
        ),
        (
            "无论遇到什么困难，我们都不能放弃。",
            "Whatever difficulties we meet, we cannot give up.",
            "무슨 어려움을 만나도 우리는 포기하면 안 된다.",
        ),
    ],
    "zh_erqie": [
        (
            "这部电影很长，而且很无聊。",
            "This film is long, and boring besides.",
            "이 영화는 길고, 게다가 지루하다.",
        ),
        (
            "他同意了，并且当场签了字。",
            "He agreed and signed on the spot.",
            "그는 동의했고, 자리에서 바로 서명했다.",
        ),
    ],
    "zh_jingran": [
        (
            "这么简单的题，他居然做错了。",
            "He actually got such an easy question wrong.",
            "그는 이렇게 쉬운 문제를 틀렸다.",
        ),
        ("没想到他竟然一个人来了。", "Unexpectedly, he came alone.", "설마 그가 혼자 올 줄은 몰랐다."),
    ],
    "zh_genju": [
        (
            "根据天气预报，明天有雪。",
            "According to the forecast it will snow tomorrow.",
            "일기 예보에 따르면 내일 눈이 온다.",
        ),
        (
            "按照规定，迟到的人要登记。",
            "According to the rules, latecomers must sign in.",
            "규정에 따라 지각자는 등록해야 한다.",
        ),
    ],
    "zh_yimian": [
        (
            "请把地址写清楚，以免寄错。",
            "Please write the address clearly, so it is not misdelivered.",
            "주소를 분명히 적어서 잘못 배달되지 않게 하세요.",
        ),
        (
            "早点儿出发，以免赶不上火车。",
            "Leave early so you do not miss the train.",
            "일찍 출발해서 기차를 놓치지 마세요.",
        ),
    ],
    "zh_bijing": [
        (
            "毕竟他还小，别要求太高。",
            "He is still young after all; do not ask too much.",
            "그는 아직 어리니까 너무 많이 요구하지 마.",
        ),
        (
            "反正路也不远，我们走着去吧。",
            "Anyway the road is not far; let us walk.",
            "어차피 길도 멀지 않으니 걸어 가자.",
        ),
    ],
    "zh_shenzhi": [
        (
            "他忙得甚至忘了吃饭。",
            "He was so busy he even forgot to eat.",
            "그는 너무 바빠서 밥 먹는 것조차 잊었다.",
        ),
        (
            "这个词甚至连老师都没见过。",
            "Even the teacher has never seen this word.",
            "이 단어는 선생님조차 본 적이 없다.",
        ),
    ],
    "zh_hekuang": [
        (
            "大人都会做好，何况是孩子。",
            "Even adults can do it well, let alone children.",
            "어른도 다 잘하는데 하물며 아이야.",
        ),
        (
            "这条路很远，况且天又晚了。",
            "The road is long, and besides it is getting late.",
            "길은 멀고, 게다가 날도 저물었다.",
        ),
    ],
    "zh_nanyi": [
        (
            "这个问题必须尽快加以解决。",
            "This problem must be addressed as soon as possible.",
            "이 문제는 가능한 한 빨리 해결해야 한다.",
        ),
        ("他的行为让人难以理解。", "His behaviour is hard to understand.", "그의 행동은 이해하기 어렵다."),
    ],
    "zh_yizhi": [
        (
            "他读得入了迷，以致忘了时间。",
            "He read so absorbed that he forgot the time.",
            "그는 너무 빠져들어서 시간을 잊었다.",
        ),
        (
            "雨下得太大了，以至比赛被迫取消。",
            "It rained so hard that the match had to be cancelled.",
            "비가 너무 많이 와서 경기가 취소되었다.",
        ),
    ],
    "zh_ershi": [
        (
            "看起来很简单，然而做起来很难。",
            "It looks simple, yet doing it is hard.",
            "보기에는 간단하지만 실제로 하기는 어렵다.",
        ),
        ("他年纪很小，却很懂事。", "He is very young, yet very sensible.", "그는 나이가 어린데도 아주 영리하다."),
    ],
}


def collect():
    "The golden set, and the sentences that do not fire on their own rule."
    rules = {
        rule["key"]: rule for rule in zh._ZH_RULES
    }  # pylint: disable=protected-access
    missing = sorted(set(rules) - set(_SENTENCES))
    golden = {}
    failures = []
    for key, rows in _SENTENCES.items():
        if key not in rules:
            failures.append(f"{key}: no such rule")
            continue
        examples = []
        for chinese, english, korean in rows:
            if not match_rule(
                {"match": rules[key]["match"]}, zh._tokens_for(chinese), chinese
            ):
                failures.append(f"{key}: does not fire on {chinese}")
                continue
            examples.append({"text": chinese, "en": english, "zh": "", "ko": korean})
        if examples:
            golden[key] = {"name": rules[key]["name"], "examples": examples}
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
