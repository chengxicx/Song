"""Verify and write the Mandarin grammar golden set.

The Mandarin engine is a hand-written rule set with no corpus behind it --
there is no ``grammar_zh.json`` -- so its golden sentences are written here
rather than derived.  Every rule gets two, each carrying an English and a
Korean gloss so the library page can show a translation on every display
language.  The rule set itself works through the HSK 3.0 syllabus' 语法大纲
(《新版HSK考试大纲（词汇、汉字、语法）》), levels 一 to 九 mapped onto
CEFR bands 一/二级→A1 … 七—九级→C2, so the sentences follow the syllabus
through those bands.

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
        (
            "我昨天买了一本书。",
            "I bought a book yesterday.",
            "나는 어제 책을 한 권 샀다.",
        ),
        (
            "天气冷了，该穿外套了。",
            "It has turned cold; time to put on a coat.",
            "날씨가 추워졌으니 외투를 입을 때다.",
        ),
    ],
    "zh_zhe": [
        (
            "门上贴着一张纸条。",
            "A note is stuck to the door.",
            "문에 쪽지가 붙어 있다.",
        ),
        (
            "他笑着跟我打招呼。",
            "He greeted me with a smile.",
            "그는 웃으며 나에게 인사했다.",
        ),
    ],
    "zh_guo": [
        (
            "我去过中国三次。",
            "I have been to China three times.",
            "나는 중국에 세 번 가 봤다.",
        ),
        (
            "这部电影我看过，不想再看了。",
            "I have seen this film; I do not want to see it again.",
            "이 영화는 봤기 때문에 다시 보고 싶지 않다.",
        ),
    ],
    "zh_bu": [
        (
            "我今天不去公司。",
            "I am not going to the office today.",
            "나는 오늘 회사에 가지 않는다.",
        ),
        (
            "这道题不难，你再想想。",
            "This problem is not hard; think again.",
            "이 문제는 어렵지 않으니 다시 생각해 봐.",
        ),
    ],
    "zh_mei": [
        (
            "我还没吃早饭。",
            "I have not eaten breakfast yet.",
            "나는 아직 아침을 먹지 않았다.",
        ),
        (
            "他昨天没有来上课。",
            "He did not come to class yesterday.",
            "그는 어제 수업에 오지 않았다.",
        ),
    ],
    "zh_zai": [
        (
            "妈妈在厨房做饭。",
            "Mum is cooking in the kitchen.",
            "엄마가 부엌에서 요리하고 계셔.",
        ),
        ("我的书在桌子上。", "My book is on the table.", "내 책은 책상 위에 있다."),
    ],
    "zh_question_words": [
        ("你在找什么？", "What are you looking for?", "무엇을 찾고 있어?"),
        (
            "这句话该怎么翻译？",
            "How should this sentence be translated?",
            "이 문장은 어떻게 번역해야 해?",
        ),
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
        (
            "我们走吧，时间不早了。",
            "Let us go; it is getting late.",
            "우리 가자, 시간이 늦었어.",
        ),
        (
            "快点儿吧，大家都等你呢。",
            "Hurry up; everyone is waiting for you.",
            "어서 해, 모두 기다리고 있어.",
        ),
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
        (
            "请把牛奶放在冰箱里。",
            "Please put the milk in the fridge.",
            "우유를 냉장고에 넣어 주세요.",
        ),
    ],
    "zh_bei_sentence": [
        (
            "我的手机被小偷偷了。",
            "My phone was stolen by a thief.",
            "내 휴대폰을 도둑맞았다.",
        ),
        (
            "这个问题终于被解决了。",
            "The problem was finally solved.",
            "그 문제는 마침내 해결되었다.",
        ),
    ],
    "zh_zhengzai": [
        (
            "他正在开会，不能接电话。",
            "He is in a meeting and cannot take the call.",
            "그는 회의 중이라 전화를 받을 수 없다.",
        ),
        (
            "妈妈正在做饭，我们等等吧。",
            "Mum is cooking; let us wait.",
            "엄마가 요리 중이니 기다리자.",
        ),
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
        (
            "这件衣服比那件更好看。",
            "This shirt looks better than that one.",
            "이 옷이 저것보다 더 예쁘다.",
        ),
        ("他是我最好的朋友。", "He is my best friend.", "그는 내 가장 친한 친구다."),
    ],
    "zh_bibi": [
        (
            "他比我高一个头。",
            "He is a head taller than me.",
            "그는 나보다 머리 하나 더 크다.",
        ),
        (
            "今年的冬天比去年冷。",
            "This winter is colder than last year's.",
            "올해 겨울은 작년보다 춥다.",
        ),
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
        (
            "雨越下越大。",
            "The rain is falling harder and harder.",
            "비가 점점 더 세게 내린다.",
        ),
        (
            "我越听越觉得你说得对。",
            "The more I listen, the more I think you are right.",
            "들을수록 네 말이 맞는 것 같아.",
        ),
    ],
    "zh_yibian_yibian": [
        (
            "他一边走路一边打电话。",
            "He talks on the phone while walking.",
            "그는 걸으면서 전화를 한다.",
        ),
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
        (
            "老师让我念课文。",
            "The teacher asked me to read the text.",
            "선생님이 나에게 본문을 읽게 했다.",
        ),
        (
            "我的帽子叫风吹跑了。",
            "My hat was blown away by the wind.",
            "내 모자는 바람에 날려갔다.",
        ),
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
        (
            "没想到他竟然一个人来了。",
            "Unexpectedly, he came alone.",
            "설마 그가 혼자 올 줄은 몰랐다.",
        ),
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
        (
            "他的行为让人难以理解。",
            "His behaviour is hard to understand.",
            "그의 행동은 이해하기 어렵다.",
        ),
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
        (
            "他年纪很小，却很懂事。",
            "He is very young, yet very sensible.",
            "그는 나이가 어린데도 아주 영리하다.",
        ),
    ],
    # ---- A1 (HSK 一、二级) ----
    "zh_dixu": [
        ("这是第三课。", "This is Lesson Three.", "이것은 제3과입니다."),
        (
            "今天是她的第十八个生日。",
            "Today is her eighteenth birthday.",
            "오늘은 그녀의 열여덟 번째 생일이다.",
        ),
    ],
    "zh_men_plural": [
        (
            "同学们都到齐了。",
            "The students have all arrived.",
            "학생들이 모두 다 도착했다.",
        ),
        (
            "孩子们在公园里玩得很开心。",
            "The children are having a great time in the park.",
            "아이들이 공원에서 아주 즐겁게 놀고 있다.",
        ),
    ],
    "zh_xiao_prefix": [
        (
            "小王是我的同学。",
            "Xiao Wang is my classmate.",
            "샤오왕은 나의 학급 친구이다.",
        ),
        (
            "小李今天没来上班。",
            "Xiao Li did not come to work today.",
            "샤오리는 오늘 출근하지 않았다.",
        ),
    ],
    "zh_nengyuan_1": [
        (
            "我会说一点儿汉语。",
            "I can speak a little Chinese.",
            "나는 중국어를 조금 할 줄 안다.",
        ),
        (
            "周末你可以来我家玩。",
            "You can come to my place this weekend.",
            "주말에 우리 집에 놀러 와도 돼.",
        ),
    ],
    "zh_nengyuan_2": [
        (
            "我想买一台新电脑。",
            "I want to buy a new computer.",
            "나는 새 컴퓨터를 한 대 사고 싶다.",
        ),
        (
            "他要学做中国菜。",
            "He wants to learn to cook Chinese food.",
            "그는 중국 요리를 배우려고 한다.",
        ),
    ],
    "zh_keneng_modal": [
        ("明天可能下雨。", "It may rain tomorrow.", "내일 비가 올지도 모른다."),
        (
            "他可能还不知道这个消息。",
            "He may not know the news yet.",
            "그는 아직 이 소식을 모를 수도 있다.",
        ),
    ],
    "zh_chengdu_1": [
        (
            "今天的天气非常好。",
            "The weather today is very good.",
            "오늘 날씨가 아주 좋다.",
        ),
        ("这部电影太长了。", "This film is too long.", "이 영화는 너무 길다."),
    ],
    "zh_dui_he": [
        ("我对他很好。", "I am very kind to him.", "나는 그에게 아주 친절하다."),
        (
            "下午我要和老师谈一谈。",
            "I will have a talk with the teacher this afternoon.",
            "오후에 선생님과 이야기를 나눌 것이다.",
        ),
    ],
    "zh_zhengfan": [
        ("你是不是累了？", "Are you tired?", "너 피곤하냐?"),
        (
            "周末你去不去爬山？",
            "Are you going climbing this weekend or not?",
            "주말에 등산 가겠느냐?",
        ),
    ],
    "zh_you_np": [
        (
            "我有一个哥哥和一个妹妹。",
            "I have an elder brother and a younger sister.",
            "나에게는 형과 여동생이 하나씩 있다.",
        ),
        (
            "桌子上有一本新书。",
            "There is a new book on the table.",
            "책상 위에 새 책이 한 권 있다.",
        ),
    ],
    "zh_liandong_1": [
        (
            "我们去超市买东西。",
            "We are going to the supermarket to shop.",
            "우리는 슈퍼에 가서 물건을 산다.",
        ),
        (
            "他来我家吃饭。",
            "He comes to my home for dinner.",
            "그는 우리 집에 와서 저녁을 먹는다.",
        ),
    ],
    "zh_shuangbin": [
        (
            "老师教我们汉语。",
            "The teacher teaches us Chinese.",
            "선생님이 우리에게 중국어를 가르친다.",
        ),
        (
            "请递给我那本书。",
            "Please pass me that book.",
            "그 책을 나에게 건네 주세요.",
        ),
    ],
    "zh_yao_kuai_le": [
        (
            "天阴得厉害，快要下雨了。",
            "The sky is heavily overcast; it is about to rain.",
            "하늘이 아주 흐려서 곧 비가 올 것 같다.",
        ),
        (
            "火车就要开了，快上车吧。",
            "The train is leaving; get on quickly.",
            "기차가 곧 떠나려고 하니 서둘러 타라.",
        ),
    ],
    "zh_dou_le": [
        (
            "都十二点了，还没睡吗？",
            "It is already twelve; are you still up?",
            "벌써 열두 시인데 아직 안 자냐?",
        ),
        (
            "你都大学生了，还这么贪玩。",
            "You are already a university student and still play around so much.",
            "너는 벌써 대학생인데 아직도 이렇게 놀기만 한다니.",
        ),
    ],
    "zh_haishi_ba": [
        ("我们还是回去吧。", "Let us just go back.", "우리는 그냥 돌아가자."),
        (
            "天太晚了，你还是住下来吧。",
            "It is too late; you had better stay the night.",
            "너무 늦었으니 그냥 묵고 가라.",
        ),
    ],
    "zh_bie": [
        (
            "别忘了带钥匙。",
            "Do not forget to take your keys.",
            "열쇠를 가져가는 것을 잊지 마라.",
        ),
        (
            "别着急，慢慢说。",
            "Do not worry; take your time.",
            "급해하지 말고 천천히 말해라.",
        ),
    ],
    "zh_cong_wang": [
        ("他从北京来。", "He comes from Beijing.", "그는 베이징에서 왔다."),
        (
            "一直往前走，到路口右转。",
            "Go straight on and turn right at the crossing.",
            "쭉 앞으로 걷다가 사거리에서 우회전해라.",
        ),
    ],
    "zh_gei_prep": [
        (
            "请给他一杯水。",
            "Please give him a glass of water.",
            "그에게 물 한 잔을 주세요.",
        ),
        (
            "妈妈给我做了一件毛衣。",
            "Mum knitted a sweater for me.",
            "엄마가 나에게 스웨터를 한 벌 만들어 주셨다.",
        ),
    ],
    "zh_gen_prep": [
        ("我跟他一起去。", "I will go together with him.", "나는 그와 함께 갈 것이다."),
        (
            "这件事你跟他商量一下吧。",
            "Discuss it with him.",
            "이 일은 그와 상의해 보아라.",
        ),
    ],
    "zh_suiran_danshi": [
        (
            "虽然很累，但是他坚持跑完了。",
            "Although he was tired, he kept running to the end.",
            "비록 피곤했지만 그는 끝까지 달렸다.",
        ),
        (
            "这件衣服很好看，但是太贵了。",
            "This dress is pretty, but too expensive.",
            "이 옷은 아주 예쁘지만 너무 비싸다.",
        ),
    ],
    "zh_yi_jiu": [
        (
            "他一到家就开始做饭。",
            "As soon as he got home, he started cooking.",
            "그는 집에 도착하자마자 요리를 시작했다.",
        ),
        (
            "我一想到考试就紧张。",
            "I get nervous whenever I think of the exam.",
            "나는 시험 생각만 하면 긴장된다.",
        ),
    ],
    "zh_gaishu_duo": [
        (
            "图书馆里有三十多本书。",
            "There are more than thirty books in the library.",
            "도서관에 책이 서른 권이 넘게 있다.",
        ),
        (
            "他在中国住了两年多。",
            "He lived in China for more than two years.",
            "그는 중국에 2년 넘게 살았다.",
        ),
    ],
    "zh_dongliangci_ci": [
        (
            "我去过两次北京。",
            "I have been to Beijing twice.",
            "나는 베이징에 두 번 가 본 적이 있다.",
        ),
        (
            "这是她第一次来上海。",
            "This is her first visit to Shanghai.",
            "이것은 그녀가 처음으로 상하이에 온 것이다.",
        ),
    ],
    "zh_mei_distribution": [
        (
            "每个人都应该遵守规则。",
            "Everyone should follow the rules.",
            "모든 사람이 규칙을 지켜야 한다.",
        ),
        ("他每天都锻炼身体。", "He exercises every day.", "그는 매일 운동을 한다."),
    ],
    "zh_xuanze_q": [
        (
            "你喝茶还是咖啡？",
            "Would you like tea or coffee?",
            "차를 마실까, 커피를 마실까?",
        ),
        (
            "我们坐火车去还是坐飞机去？",
            "Shall we go by train or by plane?",
            "우리 기차로 갈까, 비행기로 갈까?",
        ),
    ],
    "zh_verb_redup": [
        (
            "大家讨论讨论这个问题。",
            "Let us discuss this question a bit.",
            "모두 이 문제를 좀 토론해 보자.",
        ),
        (
            "让我看一看那张照片。",
            "Let me have a look at that photo.",
            "그 사진을 좀 보게 해 줘.",
        ),
    ],
    "zh_adj_redup": [
        (
            "孩子们高高兴兴地去郊游了。",
            "The children went on the outing happily.",
            "아이들은 신나게 소풍을 떠났다.",
        ),
        (
            "他慢慢地走进了教室。",
            "He walked slowly into the classroom.",
            "그는 천천히 교실로 걸어 들어갔다.",
        ),
    ],
    "zh_pinlv_1": [
        (
            "我经常骑自行车上学。",
            "I often go to school by bike.",
            "나는 자전거를 자주 타고 학교에 간다.",
        ),
        (
            "他有时在家做饭。",
            "He sometimes cooks at home.",
            "그는 때때로 집에서 요리를 한다.",
        ),
    ],
    "zh_yiqi_yikuai": [
        ("我们一起去吧。", "Let us go together.", "우리 같이 가자."),
        (
            "周末我们一块儿去看电影。",
            "Let us go to the cinema together this weekend.",
            "주말에 우리 같이 영화 보러 가자.",
        ),
    ],
    "zh_zai_fanfu": [
        ("再试一次吧。", "Give it one more try.", "한 번 더 시도해 보자."),
        (
            "这个词很难，请再说一遍。",
            "This word is hard; please say it once more.",
            "이 단어는 어려우니 한 번 더 말해 주세요.",
        ),
    ],
    "zh_de_adverbial": [
        ("他认真地学习。", "He studies earnestly.", "그는 성실히 공부한다."),
        (
            "她高兴地唱起歌来。",
            "She began to sing with joy.",
            "그녀는 기쁘게 노래를 부르기 시작했다.",
        ),
    ],
    "zh_jieguo_buYu": [
        (
            "我终于听懂了这句话。",
            "I finally understood this sentence.",
            "나는 마침내 이 문장을 알아들었다.",
        ),
        (
            "请把课文读完再休息。",
            "Finish reading the text before you rest.",
            "본문을 다 읽은 다음에 쉬어라.",
        ),
    ],
    "zh_quxiang_buYu": [
        (
            "他突然想起来钥匙放在哪儿了。",
            "He suddenly remembered where the key was.",
            "그는 문득 열쇠를 어디에 두었는지 떠올렸다.",
        ),
        (
            "请把箱子搬进来。",
            "Please carry the box in.",
            "그 상자를 안으로 옮겨 주세요.",
        ),
    ],
    "zh_zhuangtai_buYu": [
        ("她跑得飞快。", "She runs extremely fast.", "그녀는 아주 빠르게 달린다."),
        (
            "他汉语说得非常好。",
            "He speaks Chinese very well.",
            "그는 중국어를 아주 잘한다.",
        ),
    ],
    "zh_shuliang_buYu": [
        ("我等了三个小时。", "I waited for three hours.", "나는 세 시간을 기다렸다."),
        (
            "这篇课文我们读了两遍。",
            "We read this text twice.",
            "이 본문을 우리는 두 번 읽었다.",
        ),
    ],
    "zh_bijiao_you_mei": [
        (
            "他没有你这么细心。",
            "He is not as careful as you.",
            "그는 너만큼 세심하지 못하다.",
        ),
        (
            "妹妹已经有妈妈这么高了。",
            "My younger sister is already as tall as Mum.",
            "여동생은 벌써 어머니만큼 키가 크다.",
        ),
    ],
    # ---- A2 (HSK 三级) ----
    "zh_nengyuan_3": [
        ("你需要多休息。", "You need more rest.", "너는 더 쉬어야 한다."),
        ("现在该出发了。", "It is time to set off now.", "이제 출발할 때다."),
    ],
    "zh_chengdu_2": [
        ("他挺喜欢的。", "He rather likes it.", "그는 꽤 마음에 들어한다."),
        ("这道菜比较辣。", "This dish is rather spicy.", "이 요리는 좀 맵다."),
    ],
    "zh_qwh_renzhi": [
        (
            "他什么都不怕。",
            "He is afraid of nothing at all.",
            "그는 아무것도 두려워하지 않는다.",
        ),
        (
            "谁也不知道他去哪儿了。",
            "Nobody knows where he went.",
            "아무도 그가 어디에 갔는지 모른다.",
        ),
    ],
    "zh_liangci_chongdie": [
        (
            "这些苹果个个都很甜。",
            "Every one of these apples is sweet.",
            "이 사과들은 하나하나 모두 달다.",
        ),
        (
            "商店里的衣服件件都好看。",
            "Every piece of clothing in the shop looks nice.",
            "가게의 옷들은 한 벌 한 벌 모두 예쁘다.",
        ),
    ],
    "zh_fanwei_3": [
        (
            "我们一共花了五十元。",
            "We spent fifty yuan in total.",
            "우리는 모두 50위안을 썼다.",
        ),
        (
            "他只是开玩笑，你别生气。",
            "He is only joking; do not be angry.",
            "그는 그저 농담하는 것일 뿐이니 화내지 마라.",
        ),
    ],
    "zh_shijian_3": [
        (
            "我刚到家，马上就开会。",
            "I just got home and will be in a meeting very soon.",
            "나는 방금 집에 도착했는데 곧 회의가 있다.",
        ),
        (
            "他一直住在上海。",
            "He has always lived in Shanghai.",
            "그는 줄곧 상하이에 살았다.",
        ),
    ],
    "zh_xian_zaizhihou": [
        (
            "你先吃饭，然后写作业。",
            "Eat first, then do your homework.",
            "먼저 밥을 먹고 그다음에 숙제를 해라.",
        ),
        (
            "咱们先看电影，再吃饭。",
            "Let us see the film first and eat afterwards.",
            "우리 먼저 영화를 보고 나서 밥을 먹자.",
        ),
    ],
    "zh_pinlv_3": [
        ("他总是迟到。", "He is always late.", "그는 늘 지각한다."),
        (
            "周末我常常去打篮球。",
            "I often play basketball on weekends.",
            "주말에 나는 자주 농구를 한다.",
        ),
    ],
    "zh_qingtai_3": [
        (
            "明天大概会下雨。",
            "It will probably rain tomorrow.",
            "내일은 아마 비가 올 것이다.",
        ),
        (
            "你必须按时交作业。",
            "You must hand in your homework on time.",
            "너는 반드시 제시간에 숙제를 내야 한다.",
        ),
    ],
    "zh_yuqi_3": [
        ("他终于到了。", "He has finally arrived.", "그는 마침내 도착했다."),
        (
            "其实我早就知道这件事了。",
            "Actually I knew about it long ago.",
            "사실 나는 이 일을 오래전부터 알고 있었다.",
        ),
    ],
    "zh_bubi_buyong": [
        ("你不用来了。", "You need not come.", "너는 올 필요 없다."),
        (
            "这点小事，不必麻烦他。",
            "Do not bother him over such a trifle.",
            "이런 사소한 일로 그를 귀찮게 할 필요 없다.",
        ),
    ],
    "zh_huozhe": [
        (
            "周末你可以打球或者游泳。",
            "You can play ball or swim at the weekend.",
            "주말에 공놀이를 하거나 수영을 할 수 있다.",
        ),
        (
            "星期天或者看书，或者听音乐。",
            "On Sunday I either read or listen to music.",
            "일요일에는 책을 읽거나 음악을 듣는다.",
        ),
    ],
    "zh_jieci_3": [
        (
            "关于这件事，我们都同意。",
            "We all agree about this matter.",
            "이 일에 대해 우리는 모두 동의한다.",
        ),
        (
            "汽车向东开了。",
            "The car drove off eastwards.",
            "자동차가 동쪽으로 달려갔다.",
        ),
    ],
    "zh_zhiyou_cai": [
        (
            "只有努力才能进步。",
            "Only by working hard can one improve.",
            "노력해야만 진보할 수 있다.",
        ),
        (
            "只有下雨，比赛才会取消。",
            "The match will be cancelled only if it rains.",
            "비가 와야만 경기가 취소된다.",
        ),
    ],
    "zh_zhiyao_jiu": [
        (
            "只要你说实话，我就不生气。",
            "As long as you tell the truth, I will not be angry.",
            "네가 사실대로 말하기만 하면 나는 화내지 않겠다.",
        ),
        (
            "只要你努力，就一定能学好。",
            "As long as you work hard, you can surely learn it well.",
            "노력하기만 하면 반드시 잘 배울 수 있다.",
        ),
    ],
    "zh_budan": [
        (
            "他不但会说汉语，而且会说日语。",
            "He can speak not only Chinese but also Japanese.",
            "그는 중국어를 할 줄 알 뿐만 아니라 일본어도 할 줄 안다.",
        ),
        (
            "这家餐馆不但便宜，而且好吃。",
            "This restaurant is not only cheap but also tasty.",
            "이 식당은 싼 데다가 맛도 좋다.",
        ),
    ],
    "zh_keshi": [
        (
            "我想去，可是没时间。",
            "I want to go, but I have no time.",
            "가고 싶지만 시간이 없다.",
        ),
        (
            "房间不大，可是很干净。",
            "The room is not big, but very clean.",
            "방은 크지 않지만 아주 깨끗하다.",
        ),
    ],
    "zh_ranhou": [
        (
            "先洗手，然后吃饭。",
            "Wash your hands first, then eat.",
            "먼저 손을 씻고 그다음에 식사해라.",
        ),
        (
            "把材料混合，然后加热。",
            "Mix the ingredients, then heat them.",
            "재료를 섞은 다음에 가열해라.",
        ),
    ],
    "zh_dehua": [
        (
            "明天下雨的话，运动会就取消了。",
            "If it rains tomorrow, the sports meet will be cancelled.",
            "내일 비가 온다면 운동회는 취소된다.",
        ),
        (
            "时间来不及的话，我们就坐出租车去。",
            "If there is not enough time, we will take a taxi.",
            "시간이 안 된다면 우리는 택시를 타고 갈 것이다.",
        ),
    ],
    "zh_cong_qi": [
        (
            "从下个月起，我要学日语。",
            "From next month I will study Japanese.",
            "다음 달부터 나는 일본어를 배울 것이다.",
        ),
        (
            "从今年起，公园免费开放。",
            "From this year the park is free to enter.",
            "올해부터 공원은 무료로 개방된다.",
        ),
    ],
    "zh_dui_laishuo": [
        (
            "对我来说，这不算难。",
            "For me this is not really hard.",
            "나에게는 이것이 그렇게 어렵지 않다.",
        ),
        (
            "在孩子看来，一切都很新鲜。",
            "In a child's eyes everything is new.",
            "아이의 눈에는 모든 것이 새롭다.",
        ),
    ],
    "zh_yi_dou_bu": [
        ("他一点儿也不知道。", "He does not know at all.", "그는 조금도 모른다."),
        (
            "这件事他一句话都没提。",
            "He did not mention it at all.",
            "이 일에 대해 그는 한마디도 언급하지 않았다.",
        ),
    ],
    "zh_gai_le": [
        ("该吃晚饭了。", "It is time to have dinner.", "저녁 먹을 때가 되었다."),
        (
            "不早了，该回家了。",
            "It is late; time to go home.",
            "늦었으니 집에 갈 때다.",
        ),
    ],
    "zh_keneng_buYu": [
        (
            "这道题我实在看不懂。",
            "I really cannot make out this problem.",
            "이 문제는 나는 도무지 알아볼 수가 없다.",
        ),
        (
            "箱子太重，我搬不动。",
            "The box is too heavy; I cannot move it.",
            "상자가 너무 무거워서 내가 옮길 수 없다.",
        ),
    ],
    "zh_chengdu_buYu_1": [
        (
            "今天的天气好得很。",
            "The weather today is extremely fine.",
            "오늘 날씨는 아주 좋다.",
        ),
        (
            "跑了五公里，他累极了。",
            "Having run five kilometres, he is utterly tired.",
            "5킬로미터를 달려서 그는 몹시 피곤하다.",
        ),
    ],
    "zh_fanwen_bushi": [
        ("这不是很明显的吗？", "Is this not obvious?", "이것이 아주 분명하지 않은가?"),
        (
            "你不是昨天刚去过吗？",
            "Did you not just go there yesterday?",
            "너 어제 그곳에 갔다 오지 않았었냐?",
        ),
    ],
    "zh_bijiao_geng_hai": [
        (
            "今天的天气比昨天更热。",
            "Today is hotter than yesterday.",
            "오늘 날씨가 어제보다 더 덥다.",
        ),
        (
            "他跑得比我还要快。",
            "He runs even faster than I do.",
            "그는 나보다 더 빨리 달린다.",
        ),
    ],
    "zh_bijiao_yiyang": [
        ("他和我一样高。", "He is as tall as I am.", "그는 나와 키가 같다."),
        (
            "这个教室跟那个教室一样大。",
            "This classroom is as big as that one.",
            "이 교실은 저 교실만큼 크다.",
        ),
    ],
    "zh_bu_bibi": [
        (
            "她不比我高多少。",
            "She is not much taller than I am.",
            "그녀는 나보다 그렇게 크지 않다.",
        ),
        (
            "这条裤子不比那条便宜。",
            "These trousers are not cheaper than that pair.",
            "이 바지는 저 바지보다 싸지 않다.",
        ),
    ],
    "zh_bijiao_duoshao": [
        (
            "他比我多买了两本书。",
            "He bought two more books than I did.",
            "그는 나보다 책을 두 권 더 샀다.",
        ),
        (
            "你比我早到了十分钟。",
            "You arrived ten minutes earlier than I did.",
            "너는 나보다 10분 일찍 도착했다.",
        ),
    ],
    "zh_huozhe_huozhe": [
        (
            "周末或者在家看书，或者去爬山。",
            "At the weekend I either read at home or go climbing.",
            "주말에는 집에서 책을 읽거나 등산하러 간다.",
        ),
        (
            "晚上他或者散步，或者跑步。",
            "In the evening he either takes a walk or goes jogging.",
            "저녁에는 그는 산책을 하거나 달리기를 한다.",
        ),
    ],
    "zh_yihui_yihui": [
        (
            "他一会儿看书，一会儿玩手机。",
            "He reads for a while and plays with his phone for a while.",
            "그는 잠깐 책을 읽다가 잠깐 휴대폰을 만지작거린다.",
        ),
        (
            "天一会儿晴，一会儿阴。",
            "The sky is now clear, now overcast.",
            "하늘은 잠깐 맑았다가 잠깐 흐려진다.",
        ),
    ],
    "zh_you_you": [
        (
            "这个苹果又大又甜。",
            "This apple is both big and sweet.",
            "이 사과는 크고 달기까지 하다.",
        ),
        (
            "她又聪明又勤奋。",
            "She is both clever and diligent.",
            "그녀는 똑똑하고 성실하기까지 하다.",
        ),
    ],
    "zh_weile": [
        (
            "为了通过考试，他每天学习到深夜。",
            "To pass the exam he studies late into the night every day.",
            "시험에 합격하기 위해 그는 매일 밤늦게까지 공부한다.",
        ),
        (
            "为了家人，他放弃了大城市的工作。",
            "For the sake of his family he gave up the job in the big city.",
            "가족을 위해 그는 대도시의 직장을 포기했다.",
        ),
    ],
    "zh_gaishu_linjin": [
        (
            "三四个学生就够了。",
            "Three or four students will be enough.",
            "학생 세어 명이면 충분하다.",
        ),
        (
            "会议开了两三个小时。",
            "The meeting lasted two or three hours.",
            "회의가 두어 시간 동안 계속되었다.",
        ),
    ],
    "zh_cunxian_3": [
        (
            "昨天家里来了一个客人。",
            "A guest came to our home yesterday.",
            "어제 집에 손님이 한 사람 왔다.",
        ),
        (
            "墙上挂着一幅画。",
            "A picture hangs on the wall.",
            "벽에 그림이 한 폭 걸려 있다.",
        ),
    ],
    "zh_dongliangci_3": [
        ("课文读了两遍。", "The text has been read twice.", "본문을 두 번 읽었다."),
        (
            "他叹了一口气，没有说话。",
            "He gave a sigh and said nothing.",
            "그는 한숨을 한 번 쉬고 아무 말도 하지 않았다.",
        ),
    ],
    # ---- B1 (HSK 四级) ----
    "zh_gan_modal": [
        (
            "我不敢一个人走夜路。",
            "I dare not walk the night road alone.",
            "나는 감히 혼자 밤길을 걸을 수 없다.",
        ),
        (
            "你敢跟他讲道理吗？",
            "Do you dare reason with him?",
            "너는 감히 그와 이야기를 나눌 수 있겠냐?",
        ),
    ],
    "zh_ge_renhe": [
        (
            "任何人都不能进去。",
            "No one at all may go in.",
            "그 누구도 안으로 들어갈 수 없다.",
        ),
        (
            "各种口味的蛋糕都有。",
            "Cakes of every flavour are available.",
            "각양각색의 맛을 지닌 케이크가 모두 있다.",
        ),
    ],
    "zh_chengdu_4": [
        (
            "今天的作业十分多。",
            "There is a great deal of homework today.",
            "오늘 숙제가 아주 많다.",
        ),
        (
            "他尤其喜欢吃饺子。",
            "He especially likes eating dumplings.",
            "그는 특히 만두 먹기를 좋아한다.",
        ),
    ],
    "zh_fanwei_4": [
        (
            "这个班至少有三十个学生。",
            "This class has at least thirty students.",
            "이 반에는 적어도 학생이 서른 명은 있다.",
        ),
        (
            "全班共有四十五个人。",
            "The whole class has forty-five people in all.",
            "반 전체가 모두 45명이다.",
        ),
    ],
    "zh_shijian_4": [
        ("他从来不迟到。", "He is never late.", "그는 단 한 번도 지각한 적이 없다."),
        ("天忽然黑了下来。", "It suddenly grew dark.", "하늘이 문득 어두워졌다."),
    ],
    "zh_fangshi_4": [
        (
            "他故意打翻了水杯。",
            "He knocked over the glass on purpose.",
            "그는 일부러 물컵을 엎어버렸다.",
        ),
        (
            "同学们互相帮助。",
            "The classmates help each other.",
            "학급 친구들은 서로 돕는다.",
        ),
    ],
    "zh_qingtai_4": [
        (
            "也许明天会更好。",
            "Perhaps tomorrow will be better.",
            "어쩌면 내일이 더 나을지도 모른다.",
        ),
        (
            "他恐怕不能来了。",
            "I am afraid he cannot come.",
            "아마 그는 올 수 없을 것이다.",
        ),
    ],
    "zh_bing_fou": [
        (
            "他并不喜欢这首歌。",
            "He does not actually like this song.",
            "그는 이 노래를 그렇게 좋아하는 것이 아니다.",
        ),
        (
            "问题并没有那么复杂。",
            "The problem is not really that complicated.",
            "문제는 그렇게 복잡하지 않다.",
        ),
    ],
    "zh_yuqi_4": [
        (
            "你到底去不去？",
            "Are you going or not, after all?",
            "대체 너 갈 거냐 안 가냐?",
        ),
        (
            "过马路时千万要小心。",
            "Be extremely careful when crossing the road.",
            "길을 건널 때는 아주 조심해야 한다.",
        ),
    ],
    "zh_nandao": [
        ("难道你忘了吗？", "Have you really forgotten?", "설마 너 잊어버렸냐?"),
        (
            "难道这样说是对的吗？",
            "Can this way of talking really be right?",
            "이렇게 말하는 것이 과연 옳은가?",
        ),
    ],
    "zh_jieci_4": [
        (
            "对于这个问题，大家意见不同。",
            "Opinions differ on this question.",
            "이 문제에 대해 사람들의 의견이 다르다.",
        ),
        (
            "由于天气不好，航班推迟了。",
            "Owing to the bad weather the flight was delayed.",
            "날씨가 좋지 않아서 항공편이 연기되었다.",
        ),
    ],
    "zh_lianci_4": [
        (
            "他病了，因此没来上课。",
            "He was ill, and therefore did not come to class.",
            "그가 아팠기 때문에 수업에 오지 않았다.",
        ),
        (
            "房间小了点，不过很便宜。",
            "The room is a bit small; however, it is cheap.",
            "방이 좀 작지만 그래도 싸다.",
        ),
    ],
    "zh_jiran_jiu": [
        (
            "既然你来了，就一起吃饭吧。",
            "Since you are here, let us eat together.",
            "왔으니 같이 식사하자.",
        ),
        (
            "既然答应了，就应该做到。",
            "Since you promised, you should keep your word.",
            "약속했으면 반드시 지켜야 한다.",
        ),
    ],
    "zh_budan_buguang": [
        (
            "他不仅会开车，还会修车。",
            "He can not only drive but also repair cars.",
            "그는 운전만 할 줄 알 뿐 아니라 차도 고칠 줄 안다.",
        ),
        (
            "不光学生，老师也参加了比赛。",
            "Not only the students but the teachers joined the contest.",
            "학생들만이 아니라 선생님들도 대회에 참가했다.",
        ),
    ],
    "zh_shi_causative": [
        (
            "这件事使我很感动。",
            "This event moved me deeply.",
            "이 일은 나를 몹시 감동시켰다.",
        ),
        (
            "他的话使我陷入了沉思。",
            "His words plunged me into deep thought.",
            "그의 말은 나를 깊은 사색에 빠지게 했다.",
        ),
    ],
    "zh_buru": [
        (
            "他的汉语不如你。",
            "His Chinese is not as good as yours.",
            "그의 중국어는 너만큼 못하다.",
        ),
        (
            "这家餐馆不如那家干净。",
            "This restaurant is not as clean as that one.",
            "이 식당은 저 식당만큼 깨끗하지 않다.",
        ),
    ],
    "zh_xiangbi": [
        (
            "跟去年相比，今年暖和多了。",
            "Compared with last year this year is much warmer.",
            "작년에 비해 올해가 훨씬 따뜻하다.",
        ),
        (
            "与坐飞机相比，我更喜欢坐火车。",
            "Compared with flying I prefer the train.",
            "비행기를 타는 것에 비해 나는 기차 타기를 더 좋아한다.",
        ),
    ],
    "zh_shuangchong_fouding": [
        (
            "这件事我不得不告诉他。",
            "I had no choice but to tell him about it.",
            "이 일에 대해 나는 그에게 말하지 않을 수 없었다.",
        ),
        (
            "这么好的机会，他非去不可。",
            "Such a fine opportunity he simply must not miss.",
            "이렇게 좋은 기회라면 그는 가지 않을 수 없다.",
        ),
    ],
    "zh_bushi_ershi": [
        (
            "他不是不想去，而是没时间。",
            "It is not that he does not want to go; he has no time.",
            "그가 가기 싫어서가 아니라 시간이 없는 것이다.",
        ),
        (
            "成功不是靠运气，而是靠努力。",
            "Success comes not from luck but from hard work.",
            "성공은 운이 아니라 노력에 달려 있다.",
        ),
    ],
    "zh_ji_you": [
        (
            "这个房间既干净又宽敞。",
            "The room is both clean and spacious.",
            "이 방은 깨끗할 뿐만 아니라 넓기까지 하다.",
        ),
        (
            "他既会弹钢琴，也会拉小提琴。",
            "He can both play the piano and the violin.",
            "그는 피아노도 칠 줄 알고 바이올린도 켤 줄 안다.",
        ),
    ],
    "zh_yifangmian": [
        (
            "学习汉语一方面能找工作，另一方面能了解文化。",
            "Studying Chinese helps one find a job and, on the other hand, understand the culture.",
            "중국어를 배우면 한편으로는 직장을 구하는 데 도움이 되고 다른 한편으로는 문화를 이해할 수 있다.",
        ),
        (
            "他的建议一方面可行，另一方面成本太高。",
            "His suggestion is workable on the one hand but too costly on the other.",
            "그의 제안은 한편으로는 실행 가능하지만 다른 한편으로는 비용이 너무 많이 든다.",
        ),
    ],
    "zh_shouci_qici": [
        (
            "首先把材料准备好，其次注意火候。",
            "First prepare the ingredients; second, watch the heat.",
            "먼저 재료를 준비하고 그다음에 불의 세기를 주의해라.",
        ),
        (
            "做计划首先要明确目标，然后安排时间。",
            "In planning, first set the goal clearly, then arrange the time.",
            "계획을 세울 때 먼저 목표를 분명히 하고 그다음에 시간을 배정해야 한다.",
        ),
    ],
    "zh_yushi": [
        (
            "天下雨了，于是我们改在室内开会。",
            "It began to rain, so we moved the meeting indoors.",
            "비가 내리자 우리는 회의를 실내로 옮겼다.",
        ),
        (
            "他点了点头，于是大家都安静下来。",
            "He nodded, and at that everyone quieted down.",
            "그가 고개를 끄덕이자 모두가 조용해졌다.",
        ),
    ],
    "zh_lian_ye_dou": [
        (
            "他忙得连饭都没吃。",
            "He was so busy he skipped even his meal.",
            "그는 너무 바빠서 밥조차 먹지 못했다.",
        ),
        (
            "这孩子连自己的名字都不会写。",
            "The child cannot even write his own name.",
            "이 아이는 제 이름도 쓸 줄 모른다.",
        ),
    ],
    "zh_bushi_jiushi": [
        (
            "他每天不是在图书馆，就是在教室。",
            "Every day he is either in the library or in the classroom.",
            "그는 매일 도서관에 있거나 아니면 교실에 있다.",
        ),
        (
            "周末他不是踢足球，就是打篮球。",
            "At weekends he either plays football or basketball.",
            "주말이면 그는 축구를 하거나 아니면 농구를 한다.",
        ),
    ],
    "zh_jinguan": [
        (
            "尽管下着雨，比赛还是照常进行。",
            "Although it was raining, the match went ahead as usual.",
            "비가 내리고 있었지만 경기는 여전히 예정대로 진행되었다.",
        ),
        (
            "尽管很累，他还是完成了作业。",
            "Even though he was tired he finished his homework.",
            "몹시 피곤했지만 그는 숙제를 끝냈다.",
        ),
    ],
    "zh_fouze": [
        (
            "快走吧，否则赶不上火车了。",
            "Hurry up, or we will miss the train.",
            "서둘러 가라. 그러지 않으면 기차를 놓친다.",
        ),
        (
            "必须带上证件，否则不让进门。",
            "You must bring your ID, otherwise you will not be let in.",
            "신분증을 꼭 지참해야 한다. 아니면 입장할 수 없다.",
        ),
    ],
    "zh_jishi_ye": [
        (
            "即使下雨，我也要去。",
            "Even if it rains, I will still go.",
            "비가 온다 해도 나는 가겠다.",
        ),
        (
            "即使失败了，也不要灰心。",
            "Even if you fail, do not lose heart.",
            "설령 실패하더라도 낙심하지 마라.",
        ),
    ],
    "zh_jiushi_ye": [
        (
            "就是再忙，他也要锻炼。",
            "However busy he is, he still exercises.",
            "아무리 바빠도 그는 운동을 한다.",
        ),
        (
            "就是赔钱，他也要把店开下去。",
            "Even at a loss he would keep the shop open.",
            "돈을 잃는다 해도 그는 가게를 계속 열 것이다.",
        ),
    ],
    "zh_bu_ye": [
        ("不去也可以。", "Not going is fine too.", "가지 않아도 된다."),
        (
            "他不说我们也明白。",
            "We understand even if he does not say it.",
            "그가 말하지 않아도 우리는 알 수 있다.",
        ),
    ],
    "zh_hao_mudi": [
        (
            "早点出发，好赶上第一班车。",
            "Set off early so as to catch the first bus.",
            "일찍 출발해서 첫 차를 타도록 하자.",
        ),
        (
            "你大声念，好让大家都听见。",
            "Read aloud so that everyone can hear.",
            "모두가 들을 수 있도록 큰 소리로 읽어라.",
        ),
    ],
    "zh_weile_er": [
        (
            "为了梦想而努力。",
            "Strive for the sake of the dream.",
            "꿈을 위해 노력하다.",
        ),
        (
            "为了孩子而省吃俭用。",
            "Live frugally for the children's sake.",
            "아이들을 위해 아껴 쓰며 절약하다.",
        ),
    ],
    "zh_you_shenme_hao": [
        ("这有什么好看的？", "What is worth watching in this?", "이게 볼게 뭐냐?"),
        (
            "这种事有什么好争的？",
            "What is there to argue about in such a matter?",
            "이런 일에 뭘 그렇게 다투겠냐?",
        ),
    ],
    "zh_gaishu_3": [
        ("他三十来岁。", "He is some thirty years old.", "그는 서른 살 안팎이다."),
        (
            "会议大约开了两个小时。",
            "The meeting lasted about two hours.",
            "회의는 대략 두 시간 동안 계속되었다.",
        ),
    ],
    "zh_dongliangci_chang": [
        ("下了一场大雨。", "A heavy rain fell.", "큰비가 한바탕 내렸다."),
        (
            "他这趟跑了两趟邮局。",
            "He made two trips to the post office.",
            "그는 우체국에 두 번 다녀왔다.",
        ),
    ],
    "zh_guding_4a": [
        (
            "再不走就来不及了。",
            "If we do not go now it will be too late.",
            "지금 가지 않으면 시간에 못 맞춘다.",
        ),
        (
            "他钱有的是，不用担心。",
            "He has plenty of money; do not worry.",
            "그에게는 돈이 넉넉하니 걱정할 필요 없다.",
        ),
    ],
    "zh_guding_4b": [
        (
            "再也不想迟到了。",
            "I never want to be late again.",
            "다시는 지각하고 싶지 않다.",
        ),
        (
            "怎么劝他也不听。",
            "However much we persuade him, he will not listen.",
            "아무리 권해도 듣지 않는다.",
        ),
    ],
    "zh_zai_fangmian_na": [
        (
            "在学习方面，他很下功夫。",
            "In his studies he works very hard.",
            "공부 면에서 그는 많은 노력을 기울인다.",
        ),
        (
            "拿汉语来说，声调最难掌握。",
            "Take Chinese: the tones are the hardest to master.",
            "중국어를 예로 들면 성조를 익히는 것이 가장 어렵다.",
        ),
    ],
    "zh_yi_liang_biyiliang": [
        (
            "弟弟的个子一年比一年高。",
            "My younger brother grows taller year by year.",
            "남동생의 키는 해마다 점점 커진다.",
        ),
        (
            "他的成绩一次比一次好。",
            "His results get better each time.",
            "그의 성적은 한 번 할 때마다 점점 좋아진다.",
        ),
    ],
    "zh_gou_de": [
        (
            "这次旅行够累的。",
            "This trip was really tiring.",
            "이번 여행은 참 피곤한 일이었다.",
        ),
        (
            "这些行李够重的。",
            "These luggages are quite heavy.",
            "이 짐들은 상당히 무겁다.",
        ),
    ],
    "zh_chengdu_buYu_2": [
        ("今天热得厉害。", "It is terribly hot today.", "오늘은 몹시 덥다."),
        (
            "找了一整天，他累死了。",
            "Having searched all day he is dog-tired.",
            "하루 종일 찾아 다녀서 그는 몹시 피곤하다.",
        ),
    ],
    "zh_sizige_1": [
        (
            "大家一心一意地做这件事。",
            "Everyone works on it wholeheartedly.",
            "모두 한마음 한몸으로 이 일을 한다.",
        ),
        (
            "他说起话来有说有笑。",
            "He chats away, talking and laughing.",
            "그는 말하면서 웃고 떠들며 이야기한다.",
        ),
    ],
    # ---- B2 (HSK 五级) ----
    "zh_chengdu_5": [
        ("这个箱子相当重。", "This box is quite heavy.", "이 상자는 상당히 무겁다."),
        (
            "他过于紧张，发挥失常。",
            "He was overly nervous and performed below his usual level.",
            "그는 지나치게 긴장하여 평소 실력을 발휘하지 못했다.",
        ),
    ],
    "zh_shijian_5": [
        (
            "他曾经在北京住过五年。",
            "He once lived in Beijing for five years.",
            "그는 한때 베이징에서 5년을 산 적이 있다.",
        ),
        (
            "警报一响，大家立刻跑出楼去。",
            "The moment the alarm sounded everyone rushed out of the building.",
            "경보가 울리자 모두 곧바로 건물 밖으로 뛰어 나갔다.",
        ),
    ],
    "zh_pinlv_5": [
        (
            "他通常是坐地铁上班。",
            "He usually goes to work by subway.",
            "그는 평소 지하철로 출근한다.",
        ),
        (
            "他老是忘记带课本。",
            "He is always forgetting his textbook.",
            "그는 늘 교재를 가져오는 것을 잊는다.",
        ),
    ],
    "zh_fangshi_5": [
        (
            "我会尽量按时到达。",
            "I will arrive on time as far as possible.",
            "나는 될 수 있는 한 제시간에 도착하겠다.",
        ),
        (
            "校长亲自来欢迎新生。",
            "The principal came in person to welcome the new students.",
            "교장 선생님이 친히 나와서 신입생들을 환영했다.",
        ),
    ],
    "zh_yidan_jiu": [
        (
            "一旦下定决心，就不要动摇。",
            "Once you have made up your mind, do not waver.",
            "일단 결심을 하면 흔들리지 말아야 한다.",
        ),
        (
            "火一旦烧起来，就很难控制。",
            "Once a fire starts it is hard to control.",
            "불이 일단 붙으면 통제하기 어렵다.",
        ),
    ],
    "zh_sihu_fangfu": [
        (
            "他似乎知道了真相。",
            "He seems to have learned the truth.",
            "그는 진실을 알아버린 것 같다.",
        ),
        (
            "天空仿佛要下雨了。",
            "The sky looks as if it is about to rain.",
            "하늘이 마치 비가 오려는 것처럼 보인다.",
        ),
    ],
    "zh_yuqi_5": [
        ("他根本没有休息过。", "He has not rested at all.", "그는 전혀 쉰 적이 없다."),
        (
            "天果然下起雨来了。",
            "It began to rain, just as expected.",
            "과연 하늘에서 비가 내리기 시작했다.",
        ),
    ],
    "zh_jieci_5": [
        (
            "自从毕业以后，我们就没见过。",
            "We have not met since graduation.",
            "졸업한 이후 우리는 서로 만난 적이 없다.",
        ),
        (
            "他替我把行李搬上了楼。",
            "He carried the luggage upstairs for me.",
            "그는 나를 대신하여 짐을 계단 위로 옮겨 주었다.",
        ),
    ],
    "zh_lianci_5": [
        (
            "总之，大家要齐心协力。",
            "In short, everyone must work together.",
            "요컨대 모두가 힘을 합쳐야 한다.",
        ),
        (
            "假如明天下雪，我们就不去公园了。",
            "Supposing it snows tomorrow, we will not go to the park.",
            "만일 내일 눈이 온다면 우리는 공원에 가지 않을 것이다.",
        ),
    ],
    "zh_shide": [
        (
            "他像疯子似的跑来跑去。",
            "He ran about like a madman.",
            "그는 미친 사람처럼 이리저리 뛰어 다녔다.",
        ),
        (
            "她像蝴蝶似的在花丛中飞舞。",
            "She danced among the flowers like a butterfly.",
            "그녀는 나비처럼 꽃 사이에서 춤을 추었다.",
        ),
    ],
    "zh_chongdie_gushi": [
        (
            "想来想去，还是决定去。",
            "Having thought it over and over, I decided to go after all.",
            "이리저리 생각해 보다가 결국 가기로 결정했다.",
        ),
        (
            "走着走着，就到了河边。",
            "Walking on and on, we reached the river.",
            "걷고 걷다 보니 강가에 도착했다.",
        ),
    ],
    "zh_A_de_A": [
        (
            "这些玩具大的大，小的小。",
            "Of these toys the big are big and the small are small.",
            "이 장난감들은 큰 것은 크고 작은 것은 작다.",
        ),
        (
            "同学们高的高，矮的矮。",
            "Among the students the tall are tall and the short are short.",
            "학생들 중에는 키 큰 사람은 크고 작은 사람은 작다.",
        ),
    ],
    "zh_guding_5": [
        (
            "从数据来看，情况在好转。",
            "Judging from the data, things are improving.",
            "수치로 볼 때 상황이 좋아지고 있다.",
        ),
        (
            "这个委员会由九名成员组成。",
            "This committee is made up of nine members.",
            "이 위원회는 9명의 위원으로 구성되어 있다.",
        ),
    ],
    "zh_you_zhe": [
        (
            "这座城市有着悠久的历史。",
            "This city has a long history.",
            "이 도시는 유구한 역사를 지니고 있다.",
        ),
        (
            "他们之间有着深厚的友谊。",
            "A deep friendship exists between them.",
            "그들 사이에는 깊은 우정이 있다.",
        ),
    ],
    "zh_ba_yi_V": [
        (
            "他把门一关就走了。",
            "He shut the door with one push and left.",
            "그는 문을 탁 닫고 떠나버렸다.",
        ),
        (
            "他把书一放，就跑出去玩。",
            "Putting the book down at once, he ran out to play.",
            "그는 책을 딱 내려놓고는 뛰어나가 놀았다.",
        ),
    ],
    "zh_bei_gei": [
        (
            "我的杯子被他给打破了。",
            "My glass was broken by him.",
            "내 컵이 그에게 깨져 버렸다.",
        ),
        (
            "钱包被他给偷走了。",
            "The wallet was stolen by him.",
            "지갑이 그에게 훔쳐갔다.",
        ),
    ],
    "zh_bijiao_shuliang": [
        (
            "弟弟比我小三岁。",
            "My younger brother is three years younger than I am.",
            "남동생은 나보다 세 살 어리다.",
        ),
        (
            "这棵树比那棵高五米。",
            "This tree is five metres taller than that one.",
            "이 나무는 저 나무보다 5미터 더 크다.",
        ),
    ],
    "zh_keneng_bude": [
        (
            "这种地方去不得。",
            "Such a place must not be entered.",
            "이런 곳은 함부로 가서는 안 된다.",
        ),
        (
            "这么辣的菜，我可吃不得。",
            "Food this spicy I simply cannot eat.",
            "이렇게 매운 요리는 내가 도무지 먹을 수 없다.",
        ),
    ],
    "zh_chengdu_buYu_3": [
        (
            "这两天忙得不得了。",
            "These two days have been extremely busy.",
            "이 며칠은 몹시 바빴다.",
        ),
        (
            "考完试，他累得要命。",
            "After the exam he was dead tired.",
            "시험을 치르고 나서 그는 몹시 피곤했다.",
        ),
    ],
    "zh_huoshi_huoshi": [
        (
            "周末或是打球，或是游泳。",
            "At the weekend I either play ball or swim.",
            "주말에는 공놀이를 하거나 수영을 한다.",
        ),
        (
            "放假或是回老家，或是去旅行。",
            "During the holidays I either go home or travel.",
            "휴가 때는 고향에 돌아가거나 여행을 떠난다.",
        ),
    ],
    "zh_jiashe_5": [
        (
            "带把伞吧，万一下雨呢。",
            "Take an umbrella, just in case it rains.",
            "우산 하나 챙겨라. 만약 비가 오면 어쩌려고.",
        ),
        (
            "快跑吧，要不然就迟到了。",
            "Run quickly, or you will be late.",
            "서둘러 달려라. 안 그러면 지각한다.",
        ),
    ],
    "zh_yiner": [
        (
            "他学习刻苦，因而成绩优异。",
            "He studies hard and hence achieves excellent results.",
            "그는 열심히 공부하여 그 결과 성적이 우수하다.",
        ),
        (
            "河水上涨，因而桥被淹没了。",
            "The river rose and consequently the bridge was submerged.",
            "강물이 불어나서 그 결과 다리가 잠겼다.",
        ),
    ],
    "zh_napa_ye": [
        (
            "哪怕失败，也要试一次。",
            "Even if I fail, I will still give it a try.",
            "실패한다 해도 한 번 시도해 보겠다.",
        ),
        (
            "哪怕再累，妈妈也要陪孩子读书。",
            "However tired she is, the mother still reads with her child.",
            "아무리 피곤해도 어머니는 아이와 함께 책을 읽으신다.",
        ),
    ],
    "zh_budanbu_faner": [
        (
            "他不但没有灰心，反而更加努力。",
            "Far from losing heart, he worked even harder.",
            "그는 낙심하기는커녕 오히려 더 노력했다.",
        ),
        (
            "雨不但没有停，反而越下越大。",
            "The rain did not stop; on the contrary it fell harder.",
            "비는 그치기는커녕 오히려 더 세게 내렸다.",
        ),
    ],
    "zh_weideshi": [
        (
            "他每天早起，为的是背单词。",
            "He gets up early every day for the purpose of memorising words.",
            "그는 매일 일찍 일어나는데 그 목적은 단어를 외우기 위해서다.",
        ),
        (
            "我存钱，为的是买一辆自行车。",
            "I am saving money so as to buy a bicycle.",
            "나는 돈을 모으는데 그것은 자전거를 사기 위해서다.",
        ),
    ],
    "zh_meiyou_jiu_meiyou": [
        (
            "没有耕耘，就没有收获。",
            "Without sowing there is no harvest.",
            "가을하지 않으면 거두지도 못한다.",
        ),
        (
            "没有平时的练习，就没有比赛的成绩。",
            "Without regular practice there would be no results in the contest.",
            "평소의 연습이 없었다면 대회 성적도 없었을 것이다.",
        ),
    ],
    "zh_bu_bu": [
        (
            "不大不小的房间正合适。",
            "A room neither big nor small is just right.",
            "크지도 작지도 않은 방이 딱 알맞다.",
        ),
        (
            "他的话不多不少，正好说明问题。",
            "His words were neither too many nor too few, exactly enough to make the point.",
            "그의 말은 많지도 적지도 않게 딱 문제를 설명해 주었다.",
        ),
    ],
    "zh_zai_ye": [
        (
            "再难也要坚持下去。",
            "However hard it is, I will keep at it.",
            "아무리 어려워도 끝까지 해내겠다.",
        ),
        (
            "再晚也得给他打个电话。",
            "However late it is, I must still ring him.",
            "아무리 늦어도 그에게 전화를 해야 한다.",
        ),
    ],
    "zh_dongliangci_5": [
        (
            "他看了一眼手表。",
            "He glanced at his watch.",
            "그는 손목시계를 한 번 흘긋 보았다.",
        ),
        (
            "老人把一串葡萄递给孩子。",
            "The old man handed a bunch of grapes to the child.",
            "노인이 포도 한 송이를 아이에게 건넸다.",
        ),
    ],
    # ---- C1 (HSK 六级) ----
    "zh_yichang_dadu": [
        (
            "会议结束后，大家异常兴奋。",
            "After the meeting everyone was exceptionally excited.",
            "회의가 끝난 후 모두가 이상도록 흥분했다.",
        ),
        (
            "新生大都来自农村。",
            "Most of the new students come from the countryside.",
            "신입생은 대부분 농촌 출신이다.",
        ),
    ],
    "zh_shijian_6": [
        (
            "他一向守时。",
            "He has always been punctual.",
            "그는 언제나 시간을 엄수한다.",
        ),
        (
            "比赛期间他不时向观众挥手。",
            "During the match he waved to the spectators from time to time.",
            "경기 동안 그는 이따금 관중에게 손을 흔들었다.",
        ),
    ],
    "zh_yizai_zaishan": [
        (
            "老师再三强调安全。",
            "The teacher stressed safety again and again.",
            "선생님은 두세 번 안전을 강조하셨다.",
        ),
        (
            "联合国一再呼吁和平。",
            "The United Nations has repeatedly appealed for peace.",
            "유엔은 거듭 평화를 호소했다.",
        ),
    ],
    "zh_fangshi_6": [
        (
            "她特意为你准备了礼物。",
            "She prepared the gift specially for you.",
            "그녀는 특별히 너를 위해 선물을 준비했다.",
        ),
        (
            "看到这一幕，他不禁流下眼泪。",
            "At this sight he could not help shedding tears.",
            "이 장면을 보고 그는 저절로 눈물을 흘렸다.",
        ),
    ],
    "zh_weibi": [
        (
            "这个消息未必准确。",
            "This news is not necessarily accurate.",
            "이 소식이 반드시 정확한 것은 아니다.",
        ),
        (
            "贵的东西未必好。",
            "Expensive things are not necessarily good.",
            "비싼 물건이 반드시 좋은 것은 아니다.",
        ),
    ],
    "zh_yuqi_6": [
        (
            "他明明知道答案，却装作不知道。",
            "He clearly knew the answer yet pretended not to.",
            "그는 분명히 답을 알면서도 모르는 척했다.",
        ),
        (
            "与其等着，干脆自己动手。",
            "Rather than wait, I may as well do it myself.",
            "기다리기보다 차라리 직접 해 버리자.",
        ),
    ],
    "zh_zhiyu_chuwai": [
        (
            "至于费用，以后再谈。",
            "As for the cost, we will discuss it later.",
            "비용에 관해서는 나중에 논의하자.",
        ),
        (
            "除此之外，他没有别的爱好。",
            "Apart from this he has no other hobbies.",
            "이것 이외에 그는 다른 취미가 없다.",
        ),
    ],
    "zh_buliao": [
        (
            "本以为一切顺利，不料半路出了故障。",
            "Everything seemed to be going well when unexpectedly the car broke down halfway.",
            "모두 순조로울 줄 알았는데 뜻밖에 도중에 고장이 났다.",
        ),
        (
            "我们连夜赶路，不料还是迟到了。",
            "We travelled through the night, yet unexpectedly we were still late.",
            "우리는 밤새 걸었는데도 뜻밖에 늦어버렸다.",
        ),
    ],
    "zh_suo_zhu": [
        (
            "以上所说，仅供参考。",
            "What is said above is for reference only.",
            "위에서 말한 것은 참고로 삼기 바란다.",
        ),
        (
            "他讲的正是大家所想的问题。",
            "What he raised is exactly the question everyone has in mind.",
            "그가 제기한 것이 바로 모두가 생각하는 문제다.",
        ),
    ],
    "zh_yuqi_zhuci": [
        (
            "别急嘛，慢慢来啦。",
            "Do not hurry; take it easy.",
            "서두르지 말고 천천히 해라.",
        ),
        (
            "去也好，不去也罢，你都要提前说一声。",
            "Whether you go or not, tell us in advance either way.",
            "가든 가지 않든 미리 한번 말해 두어야 한다.",
        ),
    ],
    "zh_haobu_rongyi": [
        (
            "我好不容易才买到票。",
            "I got the ticket only with great difficulty.",
            "나는 간신히 표를 구했다.",
        ),
        (
            "他好容易爬上了山顶。",
            "He climbed to the top with much effort.",
            "그는 힘겹게 산 꼭대기에 올랐다.",
        ),
    ],
    "zh_nadao_ye_shi": [
        (
            "你说得对，那倒也是。",
            "You are right; that is true, come to think of it.",
            "네 말이 맞아. 생각해 보니 그렇기도 하다.",
        ),
        (
            "那倒是，人多力量大。",
            "True enough, many hands make light work.",
            "그렇긴 해. 사람이 많으면 힘도 세지.",
        ),
    ],
    "zh_suanle_dele": [
        (
            "算了，别提这件事了。",
            "Forget it; do not mention it again.",
            "됐어. 이 일은 다시 입에 담지 말자.",
        ),
        (
            "行了，得了，别哭了。",
            "That is enough; stop crying.",
            "됐다, 그만해라. 울지 마라.",
        ),
    ],
    "zh_dao_weizhi": [
        (
            "会议开到中午十二点为止。",
            "The meeting runs until twelve noon.",
            "회의는 정오 12시까지 진행된다.",
        ),
        (
            "报名到本月底为止。",
            "Registration closes at the end of this month.",
            "신청은 이번 달 말까지이다.",
        ),
    ],
    "zh_ni_yi_ju": [
        (
            "会上大家你一言，我一语，讨论得很热烈。",
            "At the meeting everyone put in a word, and the discussion grew heated.",
            "회의에서 사람들이 이쪽에서 한마디 저쪽에서 한마디 하며 토론이 뜨거웠다.",
        ),
        (
            "孩子们东一句，西一句，聊个没完。",
            "The children chatted on and on, one line here, one there.",
            "아이들은 이쪽 한마디 저쪽 한마디 하며 끝없이 떠들었다.",
        ),
    ],
    "zh_fangzhe_bu": [
        (
            "放着现成的方法不用，真是想不通。",
            "Leaving the ready-made method unused is truly puzzling.",
            "만들어져 있는 방법을 두고 쓰지 않다니 정말 이해할 수 없다.",
        ),
        (
            "放着好好的书不读，你打游戏！",
            "Leaving good books unread, you play video games!",
            "좋은 책을 두고 읽지 않고 게임이나 하고 있어!",
        ),
    ],
    "zh_gui_gui": [
        (
            "丑话说在前头，说归说，做归做。",
            "The blunt words are said first: talking is talking, and doing is doing.",
            "미리 흉보를 말해 두는데 말은 말이요 행동은 행동이다.",
        ),
        (
            "闹归闹，正事还是要办。",
            "Fun is fun, but the real business still has to be done.",
            "장난은 장난일 뿐 본래 할 일은 해야 한다.",
        ),
    ],
    "zh_kan_ni_de": [
        (
            "瞧你高兴的，嘴都合不上了。",
            "Look how pleased you are; your mouth cannot close.",
            "너 얼마나 기쁜지 입을 다물지도 못하구나.",
        ),
        (
            "看你热的，快歇一会儿吧。",
            "Look how hot and bothered you are; rest a while.",
            "너 얼마나 더워서 그러냐. 좀 쉬어라.",
        ),
    ],
    "zh_chengdu_buYu_4": [
        (
            "这个人的品质坏透了。",
            "This person's character is rotten through and through.",
            "이 사람의 품성은 철저히 나쁘다.",
        ),
        (
            "今天的天气糟透了。",
            "The weather today is utterly awful.",
            "오늘 날씨는 몹시 나쁘다.",
        ),
    ],
    "zh_yao_me_yao_me": [
        (
            "周末要么在家休息，要么出去走走。",
            "At the weekend I either rest at home or go out for a walk.",
            "주말에는 집에서 쉬거나 아니면 나가서 거닐거나 한다.",
        ),
        (
            "要么你现在道歉，要么以后别再见我。",
            "Either apologise now, or never see me again.",
            "지금 사과하든가 아니면 앞으로 나를 다시 보지 말든가 해라.",
        ),
    ],
    "zh_sui_dan": [
        (
            "事情虽小，意义却很大。",
            "Though the matter is small, its significance is great.",
            "일은 작지만 의미는 매우 크다.",
        ),
        (
            "房间虽旧，住着却很舒服。",
            "Though the room is old, it is comfortable to live in.",
            "방은 낡지만 살기는 아주 편안하다.",
        ),
    ],
    "zh_fanshi_dou": [
        (
            "凡是迟到的人都要登记。",
            "Whoever is late must register.",
            "지각하는 사람은 모두 등기를 해야 한다.",
        ),
        (
            "凡是遵守规则的孩子都受到表扬。",
            "Every child who follows the rules is praised.",
            "규칙을 지키는 아이들은 모두 칭찬을 받는다.",
        ),
    ],
    "zh_chufei_cai": [
        (
            "除非亲自动手，否则学不会。",
            "Unless you do it yourself you will never learn it.",
            "직접 손을 쓰지 않으면 배울 수 없다.",
        ),
        (
            "除非下雨，比赛才会延期。",
            "Only if it rains will the match be postponed.",
            "비가 와야만 경기가 연기된다.",
        ),
    ],
    "zh_jiusuan_ye": [
        (
            "就算不赚钱，他也要做这件事。",
            "Even if it earns nothing, he will still do it.",
            "돈을 벌지 못한다 해도 그는 이 일을 하겠다.",
        ),
        (
            "就算失败一百次，也要再试一次。",
            "Even failing a hundred times, I would still try once more.",
            "백 번 실패한다 해도 한 번 더 시도하겠다.",
        ),
    ],
    "zh_yibian": [
        (
            "请留下联系方式，以便及时通知你。",
            "Please leave contact details so that we can notify you in time.",
            "제때 통지할 수 있도록 연락처를 남겨 주세요.",
        ),
        (
            "把课文抄一遍，以便加深记忆。",
            "Copy the text out once so as to deepen the memory.",
            "기억을 깊이 하기 위해 본문을 한 번 베껴라.",
        ),
    ],
    "zh_yishi_yishi": [
        (
            "他一时清醒，一时糊涂。",
            "He is lucid one moment and confused the next.",
            "그는 한때 정신이 들었다가 한때 몽롱해진다.",
        ),
        (
            "老人一时高兴，一时忧愁。",
            "The old man is joyful one moment and sorrowful the next.",
            "노인은 한때 기뻐하다가 한때 근심한다.",
        ),
    ],
    "zh_sizige_6": [
        (
            "他在街上左顾右盼。",
            "He looked this way and that in the street.",
            "그는 거리에서 좌우를 두리번두리번 살폈다.",
        ),
        (
            "为了找工作，他东奔西走。",
            "He ran about east and west looking for a job.",
            "일자리를 찾으려고 그는 동서로 뛰어 다녔다.",
        ),
    ],
    # ---- C2 (HSK 七—九级) ----
    "zh_chengdu_79": [
        (
            "对比之下，反差分外明显。",
            "By contrast the difference is especially marked.",
            "대조해 보면 반차가 유달리 뚜렷하다.",
        ),
        (
            "他的话颇具启发性。",
            "His words are quite thought-provoking.",
            "그의 말은 적잖이 계발성을 지닌다.",
        ),
    ],
    "zh_fanwei_79": [
        (
            "与会者均为业内专家。",
            "All the participants are experts in the field.",
            "회의 참석자는 모두 해당 분야의 전문가이다.",
        ),
        (
            "文具统统装进书包。",
            "Pack all the stationery into the schoolbag.",
            "문구류를 모조리 가방에 넣어라.",
        ),
    ],
    "zh_shijian_79": [
        (
            "他向来言而有信。",
            "He has always kept his word.",
            "그는 늘 말한 대로 신용을 지킨다.",
        ),
        (
            "会议结束，与会者随即离场。",
            "When the meeting ended the participants left at once.",
            "회의가 끝나자 참석자들은 곧장 회장을 떠났다.",
        ),
    ],
    "zh_pinlv_79": [
        (
            "警报声时而急促，时而舒缓。",
            "The alarm sounded now urgent, now slow.",
            "경보 소리는 한때는 급박하고 한때는 완만했다.",
        ),
        (
            "两队战士相继到达山顶。",
            "The soldiers of the two squads reached the summit one after another.",
            "두 분대의 병사들이 잇달아 정상에 도착했다.",
        ),
    ],
    "zh_fangshi_79": [
        (
            "他毅然放弃了优厚的待遇。",
            "He resolutely gave up the generous terms.",
            "그는 결연히 후한한 대우를 포기했다.",
        ),
        (
            "他不由得叹了一口气。",
            "He sighed in spite of himself.",
            "그는 저절로 한숨을 쉬었다.",
        ),
    ],
    "zh_fouding_79": [
        ("他绝不会同意。", "He will never agree.", "그는 결단코 동의하지 않을 것이다."),
        (
            "他无可奈何，只好答应。",
            "He could do nothing but agree.",
            "그는 어쩔 수 없이 승낙할 수밖에 없었다.",
        ),
    ],
    "zh_yuqi_79": [
        (
            "难怪他不同意，原来另有隐情。",
            "No wonder he disagreed; there was a hidden reason.",
            "그가 동의하지 않은 이유가 따로 있었으니 당연하다.",
        ),
        (
            "你不妨先试试再说。",
            "You may as well try it first and see.",
            "일단 해 보고 본 다음 말하는 것이 좋겠다.",
        ),
    ],
    "zh_nai_shi": [
        (
            "这乃是不争的事实。",
            "This is a fact beyond dispute.",
            "이것은 다툼의 여지가 없는 사실이다.",
        ),
        (
            "团结乃是力量之源。",
            "Unity is the source of strength.",
            "단결이야말로 힘의 원천이다.",
        ),
    ],
    "zh_jieci_79": [
        (
            "本着合作的精神，双方达成协议。",
            "Acting in the spirit of cooperation, the two sides reached an agreement.",
            "협력의 정신에 입각하여 쌍방은 합의에 이르렀다.",
        ),
        (
            "基于以上数据，我们得出结论。",
            "Based on the above data we draw the conclusion.",
            "상기 수치에 근거하여 우리는 결론을 내린다.",
        ),
    ],
    "zh_lianci_79": [
        (
            "倘若下大雨，运动会就得延期。",
            "If it should rain heavily the sports meet will have to be postponed.",
            "만약 큰비가 내리면 운동회는 연기할 수밖에 없다.",
        ),
        (
            "带上雨衣，以防天变。",
            "Take a raincoat in case the weather changes.",
            "날이 변할 경우를 대비하여 우비를 챙겨라.",
        ),
    ],
    "zh_zhuci_79": [
        ("不过如此罢了。", "It is no more than this.", "요컨대 이것뿐이다."),
        (
            "信也罢，不信也罢，事实摆在那里。",
            "Believe it or not, the facts are there.",
            "믿든 믿지 않든 사실은 거기에 있다.",
        ),
    ],
    "zh_dongci_79": [
        (
            "他恨不得马上飞回去。",
            "He itched to fly back at once.",
            "그는 당장 날아가 버리고 싶어 안달이다.",
        ),
        (
            "这项任务可谓艰巨。",
            "This task may well be called arduous.",
            "이 임무는 이르기 어려울 정도로 막중하다.",
        ),
    ],
    "zh_huayu_biaoji": [
        (
            "总而言之，这次尝试是成功的。",
            "In short, this attempt was a success.",
            "요컨대 이번 시도는 성공적이었다.",
        ),
        (
            "换句话说，问题出在管理上。",
            "In other words, the problem lies in the management.",
            "다시 말해 문제는 경영에 있다.",
        ),
    ],
    "zh_dongbudong_jiu": [
        (
            "他动不动就发脾气。",
            "He loses his temper at every turn.",
            "그는 자꾸만 화를 낸다.",
        ),
        (
            "这机器动不动就出毛病。",
            "The machine breaks down at the slightest excuse.",
            "이 기계는 자꾸만 고장이 난다.",
        ),
    ],
    "zh_shumian_fanwen": [
        (
            "创业谈何容易？",
            "How easy can starting a business be?",
            "창업이 어찌 그리 쉬운 일이겠는가?",
        ),
        (
            "备考何至于熬夜？",
            "Preparing for an exam hardly requires staying up all night.",
            "시험 준비라고 일찌감치 밤을 새울 일이겠는가?",
        ),
    ],
    "zh_shumian_bijiao": [
        (
            "他的成绩仅次于班长。",
            "His results are second only to the monitor's.",
            "그의 성적은 반장 다음이다.",
        ),
        (
            "论资历，老李莫过于张主任。",
            "In seniority Old Li is surpassed by none but Director Zhang.",
            "연륜으로 따지면 리 선생은 장 주임만 못하다.",
        ),
    ],
    "zh_shi_er_ding": [
        (
            "结果如何，须视调查而定。",
            "The outcome must depend on the investigation.",
            "결과가 어떠할지는 조사에 달려 있다.",
        ),
        (
            "价格视市场而定。",
            "The price is set according to the market.",
            "가격은 시장에 따라 정해진다.",
        ),
    ],
    "zh_zhici_zhiji": [
        (
            "值此新年之际，谨致祝愿。",
            "On the occasion of the New Year I offer my sincere wishes.",
            "새해를 맞이하는 이 시점에 삼가 축원을 전한다.",
        ),
        (
            "值此毕业之际，同学们依依惜别。",
            "At the time of graduation the students parted reluctantly.",
            "졸업의 시점에 학생들은 미련을 남기며 헤어졌다.",
        ),
    ],
    "zh_bieti_duo": [
        (
            "别提多高兴了！",
            "You cannot imagine how happy I was!",
            "얼마나 기뻤는지 말할 나위도 없다!",
        ),
        (
            "别提多热闹了！",
            "You cannot imagine how lively it was!",
            "얼마나 흥성거렸는지 두맙것도 없다!",
        ),
    ],
    "zh_yin_er": [
        (
            "他因勤奋而成功。",
            "He succeeded through diligence.",
            "그는 부지런함으로 인하여 성공했다.",
        ),
        (
            "小村因旅游而富裕起来。",
            "The small village grew rich through tourism.",
            "작은 마을은 관광으로 인하여 부유해졌다.",
        ),
    ],
    "zh_mei_shenme_tou": [
        (
            "这本书没什么看头。",
            "This book has nothing worth reading.",
            "이 책은 읽을 만한 것이 별로 없다.",
        ),
        (
            "这个地方有什么逛头？",
            "What is there to stroll around for in this place?",
            "이곳에 뭘 거닐 만하냐?",
        ),
    ],
    "zh_bijiao_yu_guo": [
        (
            "今年产量高于去年。",
            "This year's output is higher than last year's.",
            "올해 생산량은 작년보다 높다.",
        ),
        ("实践胜过空谈。", "Practice surpasses empty talk.", "실천은 공론보다 낫다."),
    ],
    "zh_yilai_erlai": [
        (
            "一来路远，二来时间紧，我就没去。",
            "For one thing the road was long, for another time was short, so I did not go.",
            "첫째는 길이 멀고 둘째는 시간이 촉박하여 나는 가지 않았다.",
        ),
        (
            "一则太贵，二则不实用，这台机器没买。",
            "Partly it was too dear, partly impractical, so we did not buy the machine.",
            "하나는 너무 비싸고 하나는 실용성이 없어 이 기계를 사지 않았다.",
        ),
    ],
    "zh_suowei_jiushi": [
        (
            "所谓素质，就是全面的修养。",
            "So-called quality education means all-round cultivation.",
            "소위 자질이라는 것은 바로 전면적인 수양이다.",
        ),
        (
            "所谓幸福，就是内心的满足。",
            "So-called happiness is contentment of the heart.",
            "소위 행복이라는 것은 바로 마음속 만족이다.",
        ),
    ],
    "zh_huo_huo": [
        (
            "或登山，或看海，各有乐趣。",
            "Whether climbing mountains or viewing the sea, each has its pleasure.",
            "등산를 하든 바다를 보든 저마다 재미가 있다.",
        ),
        (
            "课余或读书，或运动，生活充实。",
            "After class I either read or take exercise; life is full.",
            "수업 밖에는 책을 읽거나 운동을 하여 생활이 충실하다.",
        ),
    ],
    "zh_yuqi_ningke": [
        (
            "与其坐等机会，不如主动出击。",
            "Rather than sit waiting for a chance, better to take the initiative.",
            "기회를 앉아서 기다리기보다 주동적으로 나서는 편이 낫다.",
        ),
        (
            "与其抱怨，不如改变自己。",
            "Rather than complain, better to change yourself.",
            "불평하기보다 자신을 바꾸는 편이 낫다.",
        ),
    ],
    "zh_biekan_suishuo": [
        (
            "别看他年纪小，懂得可不少。",
            "Do not be fooled by his youth; he knows a great deal.",
            "그가 나이가 어리다고 보지 마라. 아는 것이 적잖다.",
        ),
        (
            "虽说房子旧了些，住着倒也自在。",
            "Though the house is somewhat old, living in it is quite easy.",
            "집이 좀 낡다지만 사는 것은 오히려 편하다.",
        ),
    ],
    "zh_wulun_yufou": [
        (
            "无论同意与否，请及时告知。",
            "Whether you agree or not, please tell us in good time.",
            "동의하든 하지 않든 제때 알려 주기 바란다.",
        ),
        (
            "无论参加与否，都要交报名表。",
            "Whether taking part or not, hand in the form all the same.",
            "참가하든 하지 않든 신청서를 내야 한다.",
        ),
    ],
    "zh_fan_zhe_jun": [
        (
            "凡报名者，均可参加。",
            "All who sign up may take part.",
            "신청하는 사람은 모두 참가할 수 있다.",
        ),
        (
            "凡成绩合格者，均发给证书。",
            "Everyone whose results pass will be issued a certificate.",
            "성적이 합격한 사람은 모두 증서를 발급받는다.",
        ),
    ],
    "zh_jiaruo": [
        (
            "假若明天天晴，我们就出发。",
            "Should it be fine tomorrow, we will set out.",
            "만약 내일 날씨가 맑으면 우리는 출발한다.",
        ),
        (
            "假使没有你的帮助，事情不会这么顺利。",
            "Supposing you had not helped, things would not have gone so smoothly.",
            "만일 네 도움이 없었다면 일이 이렇게 순조롭지 않았을 것이다.",
        ),
    ],
    "zh_zongran": [
        (
            "纵然困难重重，也要迎难而上。",
            "Even though difficulties abound, we must face them head on.",
            "비록 어려움이 겹겹이 있다 해도 맞서 나아가야 한다.",
        ),
        (
            "纵使失败，也虽败犹荣。",
            "Even in defeat there is honour in the effort.",
            "설령 실패하더라도 그것도 영광이다.",
        ),
    ],
    "zh_gu_lianjie": [
        (
            "天色已晚，故先行歇息。",
            "It was already late, so we rested first.",
            "해가 이미 늦었으므로 먼저 쉬었다.",
        ),
        (
            "雨势过大，故比赛延期。",
            "The rain was too heavy; therefore the match was postponed.",
            "비가 너무 많이 왔으므로 경기를 연기했다.",
        ),
    ],
    "zh_bieshuo_dou": [
        (
            "别说小学题，大学生都未必会做。",
            "Never mind primary-school problems; even university students may not manage them.",
            "초등학교 문제는 말할 것도 없고 대학생조차 풀지 못할 수도 있다.",
        ),
        (
            "别说跑步了，他连走路都困难。",
            "Let alone running, he can hardly walk.",
            "달리기는 말할 것도 없고 그는 걷는 것조차 힘들다.",
        ),
    ],
    "zh_bushuo_hai": [
        (
            "他住得远不说，还没有电梯。",
            "He lives far away, and there is no lift into the bargain.",
            "그는 살집이 멀기는커녕 엘리베이터도 없다.",
        ),
        (
            "菜贵不说，还不好吃。",
            "The food is dear, and not tasty besides.",
            "요리는 비쌀뿐 아니라 맛도 없다.",
        ),
    ],
    "zh_shangqie_hekuang": [
        (
            "老教师尚且觉得难，何况新手。",
            "Even the veteran teacher finds it hard, let alone a beginner.",
            "노교사조차 어렵다고 느끼는데 하물며 초보자야.",
        ),
        (
            "大人尚且算不过来，何况孩子。",
            "Even adults cannot work it out, let alone children.",
            "어른조차 계산하지 못하는데 하물며 아이야.",
        ),
    ],
    "zh_wei_qijian": [
        (
            "为慎重起见，再核对一遍。",
            "For the sake of caution, check it once more.",
            "신중을 기하기 위해 다시 한번 대조해 보라.",
        ),
        (
            "为安全起见，请系好安全带。",
            "For safety's sake, fasten your seat belt.",
            "안전을 위해 안전띠를 매 주기 바란다.",
        ),
    ],
    "zh_ren_ye": [
        (
            "任凭风浪起，也要稳住阵脚。",
            "However the storms rise, hold your position steady.",
            "아무리 풍파가 일어나도 진지를 굳게 지켜야 한다.",
        ),
        (
            "任他说什么，我也不动摇。",
            "Let him say what he will, I will not waver.",
            "그가 무슨 말을 하든 나는 흔들리지 않겠다.",
        ),
    ],
    "zh_feide_cai": [
        (
            "他非得亲眼看到才肯相信。",
            "He will not believe it until he sees it with his own eyes.",
            "그는 자기 눈으로 직접 보고야 믿는다.",
        ),
        (
            "这道菜非得慢火炖才入味。",
            "This dish must be simmered slowly to take the flavour.",
            "이 요리는 약한 불로 오래 끓여야만 맛이 든다.",
        ),
    ],
    "zh_ningke_yebu": [
        (
            "宁可吃亏，也不占人便宜。",
            "I would rather suffer a loss than take advantage of others.",
            "손해를 보더라도 남의 것을 차지하지 않겠다.",
        ),
        (
            "他宁可步行，也不肯花钱打车。",
            "He would rather walk than pay for a taxi.",
            "그는 걸어 가더라도 돈을 들여 택시를 타려 하지 않는다.",
        ),
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
