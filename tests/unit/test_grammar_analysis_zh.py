"""Tests for the Mandarin grammar-analysis engine (no external dependency)."""

from lute.read.render.grammar_analysis_zh import _ZH_RULES, analyze_chinese
from lute.read.render.grammar_analysis import is_mandarin_chinese_language


def _keys(text, lang="en"):
    return {e["key"] for e in analyze_chinese(text, lang)}


# One short, natural sentence per rule.
_RULE_SENTENCES = {
    "zh_le": "我吃了饭。",
    "zh_zhe": "他坐着看书。",
    "zh_guo": "我去过中国。",
    "zh_bu": "我不去。",
    "zh_mei": "我没去。",
    "zh_zai": "我在学校。",
    "zh_question_words": "你想吃什么？",
    "zh_ma": "你去吗？",
    "zh_ne": "你呢？",
    "zh_ba_q": "我们走吧。",
    "zh_ye_dou": "他们也都是学生。",
    "zh_ge": "一个人在家。",
    "zh_shi_de": "他是昨天来的。",
    "zh_ba_sentence": "他把作业写完了。",
    "zh_bei_sentence": "杯子被弟弟打破了。",
    "zh_zhengzai": "他正在看书。",
    "zh_cai_jiu": "他五点就来了。",
    "zh_geng_zui": "这个最好。",
    "zh_bibi": "他比我高。",
    "zh_yinwei_suoyi": "因为下雨，所以我不去。",
    "zh_hai": "他还在看书。",
    "zh_yueyue": "书越看越有趣。",
    "zh_yibian_yibian": "我们一边走一边聊。",
    "zh_ruguo": "如果我有钱，就去旅行。",
    "zh_chule": "除了他，大家都知道。",
    "zh_yijing": "他已经到了。",
    "zh_zhiqian_zhihou": "下课以后我去找你。",
    "zh_rang_jiao": "妈妈让我去买菜。",
    "zh_wulun": "不管多忙，他都要来。",
    "zh_erqie": "书很好，而且便宜。",
    "zh_jingran": "他竟然来了。",
    "zh_genju": "根据天气预报，明天有雨。",
    "zh_yimian": "早点走，以免堵车。",
    "zh_bijing": "他毕竟还是个孩子。",
    "zh_shenzhi": "他甚至连水都没喝。",
    "zh_hekuang": "大人都会出错，何况孩子。",
    "zh_nanyi": "这个问题难以回答。",
    "zh_yizhi": "大意以致失败。",
    "zh_ershi": "然而事实并非如此。",
    # ---- A1 (HSK 一、二级) ----
    "zh_dixu": "这是第三课。",
    "zh_men_plural": "同学们都到齐了。",
    "zh_xiao_prefix": "小王是我的同学。",
    "zh_nengyuan_1": "我会说一点儿汉语。",
    "zh_nengyuan_2": "我想买一台新电脑。",
    "zh_keneng_modal": "明天可能下雨。",
    "zh_chengdu_1": "今天的天气非常好。",
    "zh_dui_he": "我对他很好。",
    "zh_zhengfan": "你是不是累了？",
    "zh_you_np": "我有一个哥哥和一个妹妹。",
    "zh_liandong_1": "我们去超市买东西。",
    "zh_shuangbin": "老师教我们汉语。",
    "zh_yao_kuai_le": "天阴得厉害，快要下雨了。",
    "zh_dou_le": "都十二点了，还没睡吗？",
    "zh_haishi_ba": "我们还是回去吧。",
    "zh_bie": "别忘了带钥匙。",
    "zh_cong_wang": "他从北京来。",
    "zh_gei_prep": "请给他一杯水。",
    "zh_gen_prep": "我跟他一起去。",
    "zh_suiran_danshi": "虽然很累，但是他坚持跑完了。",
    "zh_yi_jiu": "他一到家就开始做饭。",
    "zh_gaishu_duo": "图书馆里有三十多本书。",
    "zh_dongliangci_ci": "我去过两次北京。",
    "zh_mei_distribution": "每个人都应该遵守规则。",
    "zh_xuanze_q": "你喝茶还是咖啡？",
    "zh_verb_redup": "大家讨论讨论这个问题。",
    "zh_adj_redup": "孩子们高高兴兴地去郊游了。",
    "zh_pinlv_1": "我经常骑自行车上学。",
    "zh_yiqi_yikuai": "我们一起去吧。",
    "zh_zai_fanfu": "再试一次吧。",
    "zh_de_adverbial": "他认真地学习。",
    "zh_jieguo_buYu": "我终于听懂了这句话。",
    "zh_quxiang_buYu": "他突然想起来钥匙放在哪儿了。",
    "zh_zhuangtai_buYu": "她跑得飞快。",
    "zh_shuliang_buYu": "我等了三个小时。",
    "zh_bijiao_you_mei": "他没有你这么细心。",
    # ---- A2 (HSK 三级) ----
    "zh_nengyuan_3": "你需要多休息。",
    "zh_chengdu_2": "他挺喜欢的。",
    "zh_qwh_renzhi": "他什么都不怕。",
    "zh_liangci_chongdie": "这些苹果个个都很甜。",
    "zh_fanwei_3": "我们一共花了五十元。",
    "zh_shijian_3": "我刚到家，马上就开会。",
    "zh_xian_zaizhihou": "你先吃饭，然后写作业。",
    "zh_pinlv_3": "他总是迟到。",
    "zh_qingtai_3": "明天大概会下雨。",
    "zh_yuqi_3": "他终于到了。",
    "zh_bubi_buyong": "你不用来了。",
    "zh_huozhe": "周末你可以打球或者游泳。",
    "zh_jieci_3": "关于这件事，我们都同意。",
    "zh_zhiyou_cai": "只有努力才能进步。",
    "zh_zhiyao_jiu": "只要你说实话，我就不生气。",
    "zh_budan": "他不但会说汉语，而且会说日语。",
    "zh_keshi": "我想去，可是没时间。",
    "zh_ranhou": "先洗手，然后吃饭。",
    "zh_dehua": "明天下雨的话，运动会就取消了。",
    "zh_cong_qi": "从下个月起，我要学日语。",
    "zh_dui_laishuo": "对我来说，这不算难。",
    "zh_yi_dou_bu": "他一点儿也不知道。",
    "zh_gai_le": "该吃晚饭了。",
    "zh_keneng_buYu": "这道题我实在看不懂。",
    "zh_chengdu_buYu_1": "今天的天气好得很。",
    "zh_fanwen_bushi": "这不是很明显的吗？",
    "zh_bijiao_geng_hai": "今天的天气比昨天更热。",
    "zh_bijiao_yiyang": "他和我一样高。",
    "zh_bu_bibi": "她不比我高多少。",
    "zh_bijiao_duoshao": "他比我多买了两本书。",
    "zh_huozhe_huozhe": "周末或者在家看书，或者去爬山。",
    "zh_yihui_yihui": "他一会儿看书，一会儿玩手机。",
    "zh_you_you": "这个苹果又大又甜。",
    "zh_weile": "为了通过考试，他每天学习到深夜。",
    "zh_gaishu_linjin": "三四个学生就够了。",
    "zh_cunxian_3": "昨天家里来了一个客人。",
    "zh_dongliangci_3": "课文读了两遍。",
    # ---- B1 (HSK 四级) ----
    "zh_gan_modal": "我不敢一个人走夜路。",
    "zh_ge_renhe": "任何人都不能进去。",
    "zh_chengdu_4": "今天的作业十分多。",
    "zh_fanwei_4": "这个班至少有三十个学生。",
    "zh_shijian_4": "他从来不迟到。",
    "zh_fangshi_4": "他故意打翻了水杯。",
    "zh_qingtai_4": "也许明天会更好。",
    "zh_bing_fou": "他并不喜欢这首歌。",
    "zh_yuqi_4": "你到底去不去？",
    "zh_nandao": "难道你忘了吗？",
    "zh_jieci_4": "对于这个问题，大家意见不同。",
    "zh_lianci_4": "他病了，因此没来上课。",
    "zh_jiran_jiu": "既然你来了，就一起吃饭吧。",
    "zh_budan_buguang": "他不仅会开车，还会修车。",
    "zh_shi_causative": "这件事使我很感动。",
    "zh_buru": "他的汉语不如你。",
    "zh_xiangbi": "跟去年相比，今年暖和多了。",
    "zh_shuangchong_fouding": "这件事我不得不告诉他。",
    "zh_bushi_ershi": "他不是不想去，而是没时间。",
    "zh_ji_you": "这个房间既干净又宽敞。",
    "zh_yifangmian": "学习汉语一方面能找工作，另一方面能了解文化。",
    "zh_shouci_qici": "首先把材料准备好，其次注意火候。",
    "zh_yushi": "天下雨了，于是我们改在室内开会。",
    "zh_lian_ye_dou": "他忙得连饭都没吃。",
    "zh_bushi_jiushi": "他每天不是在图书馆，就是在教室。",
    "zh_jinguan": "尽管下着雨，比赛还是照常进行。",
    "zh_fouze": "快走吧，否则赶不上火车了。",
    "zh_jishi_ye": "即使下雨，我也要去。",
    "zh_jiushi_ye": "就是再忙，他也要锻炼。",
    "zh_bu_ye": "不去也可以。",
    "zh_hao_mudi": "早点出发，好赶上第一班车。",
    "zh_weile_er": "为了梦想而努力。",
    "zh_you_shenme_hao": "这有什么好看的？",
    "zh_gaishu_3": "他三十来岁。",
    "zh_dongliangci_chang": "下了一场大雨。",
    "zh_guding_4a": "再不走就来不及了。",
    "zh_guding_4b": "再也不想迟到了。",
    "zh_zai_fangmian_na": "在学习方面，他很下功夫。",
    "zh_yi_liang_biyiliang": "弟弟的个子一年比一年高。",
    "zh_gou_de": "这次旅行够累的。",
    "zh_chengdu_buYu_2": "今天热得厉害。",
    "zh_sizige_1": "大家一心一意地做这件事。",
    # ---- B2 (HSK 五级) ----
    "zh_chengdu_5": "这个箱子相当重。",
    "zh_shijian_5": "他曾经在北京住过五年。",
    "zh_pinlv_5": "他通常是坐地铁上班。",
    "zh_fangshi_5": "我会尽量按时到达。",
    "zh_yidan_jiu": "一旦下定决心，就不要动摇。",
    "zh_sihu_fangfu": "他似乎知道了真相。",
    "zh_yuqi_5": "他根本没有休息过。",
    "zh_jieci_5": "自从毕业以后，我们就没见过。",
    "zh_lianci_5": "总之，大家要齐心协力。",
    "zh_shide": "他像疯子似的跑来跑去。",
    "zh_chongdie_gushi": "想来想去，还是决定去。",
    "zh_A_de_A": "这些玩具大的大，小的小。",
    "zh_guding_5": "从数据来看，情况在好转。",
    "zh_you_zhe": "这座城市有着悠久的历史。",
    "zh_ba_yi_V": "他把门一关就走了。",
    "zh_bei_gei": "我的杯子被他给打破了。",
    "zh_bijiao_shuliang": "弟弟比我小三岁。",
    "zh_keneng_bude": "这种地方去不得。",
    "zh_chengdu_buYu_3": "这两天忙得不得了。",
    "zh_huoshi_huoshi": "周末或是打球，或是游泳。",
    "zh_jiashe_5": "带把伞吧，万一下雨呢。",
    "zh_yiner": "他学习刻苦，因而成绩优异。",
    "zh_napa_ye": "哪怕失败，也要试一次。",
    "zh_budanbu_faner": "他不但没有灰心，反而更加努力。",
    "zh_weideshi": "他每天早起，为的是背单词。",
    "zh_meiyou_jiu_meiyou": "没有耕耘，就没有收获。",
    "zh_bu_bu": "不大不小的房间正合适。",
    "zh_zai_ye": "再难也要坚持下去。",
    "zh_dongliangci_5": "他看了一眼手表。",
    # ---- C1 (HSK 六级) ----
    "zh_yichang_dadu": "会议结束后，大家异常兴奋。",
    "zh_shijian_6": "他一向守时。",
    "zh_yizai_zaishan": "老师再三强调安全。",
    "zh_fangshi_6": "她特意为你准备了礼物。",
    "zh_weibi": "这个消息未必准确。",
    "zh_yuqi_6": "他明明知道答案，却装作不知道。",
    "zh_zhiyu_chuwai": "至于费用，以后再谈。",
    "zh_buliao": "本以为一切顺利，不料半路出了故障。",
    "zh_suo_zhu": "以上所说，仅供参考。",
    "zh_yuqi_zhuci": "别急嘛，慢慢来啦。",
    "zh_haobu_rongyi": "我好不容易才买到票。",
    "zh_nadao_ye_shi": "你说得对，那倒也是。",
    "zh_suanle_dele": "算了，别提这件事了。",
    "zh_dao_weizhi": "会议开到中午十二点为止。",
    "zh_ni_yi_ju": "会上大家你一言，我一语，讨论得很热烈。",
    "zh_fangzhe_bu": "放着现成的方法不用，真是想不通。",
    "zh_gui_gui": "丑话说在前头，说归说，做归做。",
    "zh_kan_ni_de": "瞧你高兴的，嘴都合不上了。",
    "zh_chengdu_buYu_4": "这个人的品质坏透了。",
    "zh_yao_me_yao_me": "周末要么在家休息，要么出去走走。",
    "zh_sui_dan": "事情虽小，意义却很大。",
    "zh_fanshi_dou": "凡是迟到的人都要登记。",
    "zh_chufei_cai": "除非亲自动手，否则学不会。",
    "zh_jiusuan_ye": "就算不赚钱，他也要做这件事。",
    "zh_yibian": "请留下联系方式，以便及时通知你。",
    "zh_yishi_yishi": "他一时清醒，一时糊涂。",
    "zh_sizige_6": "他在街上左顾右盼。",
    # ---- C2 (HSK 七—九级) ----
    "zh_chengdu_79": "对比之下，反差分外明显。",
    "zh_fanwei_79": "与会者均为业内专家。",
    "zh_shijian_79": "他向来言而有信。",
    "zh_pinlv_79": "警报声时而急促，时而舒缓。",
    "zh_fangshi_79": "他毅然放弃了优厚的待遇。",
    "zh_fouding_79": "他绝不会同意。",
    "zh_yuqi_79": "难怪他不同意，原来另有隐情。",
    "zh_nai_shi": "这乃是不争的事实。",
    "zh_jieci_79": "本着合作的精神，双方达成协议。",
    "zh_lianci_79": "倘若下大雨，运动会就得延期。",
    "zh_zhuci_79": "不过如此罢了。",
    "zh_dongci_79": "他恨不得马上飞回去。",
    "zh_huayu_biaoji": "总而言之，这次尝试是成功的。",
    "zh_dongbudong_jiu": "他动不动就发脾气。",
    "zh_shumian_fanwen": "创业谈何容易？",
    "zh_shumian_bijiao": "他的成绩仅次于班长。",
    "zh_shi_er_ding": "结果如何，须视调查而定。",
    "zh_zhici_zhiji": "值此新年之际，谨致祝愿。",
    "zh_bieti_duo": "别提多高兴了！",
    "zh_yin_er": "他因勤奋而成功。",
    "zh_mei_shenme_tou": "这本书没什么看头。",
    "zh_bijiao_yu_guo": "今年产量高于去年。",
    "zh_yilai_erlai": "一来路远，二来时间紧，我就没去。",
    "zh_suowei_jiushi": "所谓素质，就是全面的修养。",
    "zh_huo_huo": "或登山，或看海，各有乐趣。",
    "zh_yuqi_ningke": "与其坐等机会，不如主动出击。",
    "zh_biekan_suishuo": "别看他年纪小，懂得可不少。",
    "zh_wulun_yufou": "无论同意与否，请及时告知。",
    "zh_fan_zhe_jun": "凡报名者，均可参加。",
    "zh_jiaruo": "假若明天天晴，我们就出发。",
    "zh_zongran": "纵然困难重重，也要迎难而上。",
    "zh_gu_lianjie": "天色已晚，故先行歇息。",
    "zh_bieshuo_dou": "别说小学题，大学生都未必会做。",
    "zh_bushuo_hai": "他住得远不说，还没有电梯。",
    "zh_shangqie_hekuang": "老教师尚且觉得难，何况新手。",
    "zh_wei_qijian": "为慎重起见，再核对一遍。",
    "zh_ren_ye": "任凭风浪起，也要稳住阵脚。",
    "zh_feide_cai": "他非得亲眼看到才肯相信。",
    "zh_ningke_yebu": "宁可吃亏，也不占人便宜。",
}


def test_each_rule_matches_its_sentence():
    "Every rule must fire on its illustrative sentence."
    for key, sentence in _RULE_SENTENCES.items():
        hits = _keys(sentence)
        assert key in hits, f"rule {key} did not fire on: {sentence}"


def test_match_offsets_are_precise():
    "Matched offsets point at the pattern, not the whole sentence."
    for e in analyze_chinese("他把作业写完了。"):
        if e["key"] == "zh_ba_sentence":
            example = e["examples"][0]
            m = example["matches"][0]
            assert example["sentence"][m["start"] : m["end"]] == "把作业写完了"


def test_display_language_switches_desc():
    "Chinese display uses the hand-written zh description."
    zh = {e["key"]: e for e in analyze_chinese("我吃了饭。", "zh")}
    en = {e["key"]: e for e in analyze_chinese("我吃了饭。", "en")}
    assert "完成" in zh["zh_le"]["desc"]
    assert "completed" in en["zh_le"]["desc"]


def test_levels_are_cefr():
    "The Mandarin engine grades points with CEFR bands."
    levels = {
        e["key"]: e["level"]
        for e in analyze_chinese(" ".join(_RULE_SENTENCES.values()))
    }
    assert levels["zh_le"] == "A1"
    assert levels["zh_ba_sentence"] == "A2"


def test_analyze_output_structure():
    "Each entry exposes name/desc/examples with sentence and matches."
    for entry in analyze_chinese("我吃了饭。他是昨天来的。"):
        assert entry["name"]
        assert entry["desc"]
        assert entry["examples"]
        ex = entry["examples"][0]
        assert ex["sentence"]
        assert len(ex["matches"]) >= 1
        for m in ex["matches"]:
            assert m["start"] < m["end"]


class StubLanguage:
    def __init__(self, parser_type=None, name=None):
        self.parser_type = parser_type
        self.name = name


def test_is_mandarin_chinese_language_detection():
    "Mandarin detection by parser type or name, excluding Classical Chinese."
    assert is_mandarin_chinese_language(StubLanguage(parser_type="lute_mandarin"))
    assert is_mandarin_chinese_language(StubLanguage(name="Mandarin Chinese"))
    assert is_mandarin_chinese_language(StubLanguage(name="中文"))
    assert not is_mandarin_chinese_language(StubLanguage(name="Classical Chinese"))
    assert not is_mandarin_chinese_language(StubLanguage(name="Cantonese"))
    assert not is_mandarin_chinese_language(None)


def test_rules_are_loaded():
    "The rule set is populated."
    assert len(_ZH_RULES) >= 30
