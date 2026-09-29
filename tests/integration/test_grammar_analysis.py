"""
可行性原型：/read/grammar_analysis 接口的冒烟测试。

English/Spanish/Russian books route to their dedicated spaCy / pymorphy3
engines (skipped when the optional dependency isn't installed); every
other language falls back to the generic regex rule library.
"""

import json
import re
import unicodedata
import pytest
from lute.db import db
from lute.parse.registry import is_supported
from tests.utils import make_book


def test_english_grammar_analysis_uses_en_engine(client, empty_db, english):
    "英语书籍应走 spaCy 语法引擎，返回 CEFR 分级规则与例句。"
    pytest.importorskip("spacy")
    pytest.importorskip("en_core_web_sm")
    book = make_book(
        "English Grammar Demo",
        [
            "The box is too heavy to lift. She is as tall as her brother. "
            "They went home early."
        ],
        english,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "en_too_to" in keys
    assert "en_as_as" in keys
    assert "en_past_simple" in keys
    for g in data:
        assert g["level"] in ("A1", "A2", "B1", "B2", "C1", "C2"), "英语语法点应标注 CEFR"
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_spanish_grammar_analysis_uses_es_engine(client, empty_db, spanish):
    "西班牙语书籍应走 spaCy 语法引擎。"
    pytest.importorskip("spacy")
    pytest.importorskip("es_core_news_sm")
    book = make_book(
        "Spanish Grammar Demo",
        ["Hay un problema grave. Ayer comió paella. Voy a comer ahora."],
        spanish,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "es_hay" in keys
    assert "es_preterite" in keys
    assert "es_ir_a" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_russian_grammar_analysis_uses_ru_engine(client, empty_db, russian):
    "俄语书籍应走 pymorphy3 语法引擎。"
    pytest.importorskip("pymorphy3")
    book = make_book(
        "Russian Grammar Demo",
        ["У меня есть время. Мы живём в Москве. Вчера я читал книгу."],
        russian,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "ru_u_menya" in keys
    assert "ru_prep_loct" in keys
    assert "ru_past" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_french_grammar_analysis_uses_fr_engine(client, empty_db, french):
    "法语书籍应走 spaCy 语法引擎。"
    pytest.importorskip("spacy")
    pytest.importorskip("fr_core_news_sm")
    book = make_book(
        "French Grammar Demo",
        ["Il y a un café. Hier j'ai mangé une pomme. Je ne sais pas."],
        french,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "fr_il_y_a" in keys
    assert "fr_passe_compose" in keys
    assert "fr_negation" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_german_grammar_analysis_uses_de_engine(client, empty_db, german):
    "德语书籍应走 spaCy 语法引擎。"
    pytest.importorskip("spacy")
    pytest.importorskip("de_core_news_sm")
    book = make_book(
        "German Grammar Demo",
        ["Es gibt einen Park. Ich kann heute nicht kommen. Sie war müde."],
        german,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "de_es_gibt" in keys
    assert "de_modals" in keys
    assert "de_praeteritum" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_thai_grammar_analysis_uses_th_engine(client, empty_db, thai):
    "泰语书籍应走 pythainlp 语法引擎。"
    # The Thai grammar engine lives in this repo, but the Thai *parser* does
    # not: it ships in the lute3-thai plugin (entry point lute_thai), a
    # separate distribution -- the [thai] extra only installs pythainlp, so
    # importing pythainlp is not enough to build a Thai book.  Lute installs
    # the plugin on demand at runtime, so a missing plugin is a skip, not a
    # failure.  Same guard as the Korean/Mandarin/Cantonese tests below; this
    # one used to check pythainlp instead and blew up with "Unknown parser
    # type 'lute_thai'" wherever the extra was installed without the plugin.
    if not is_supported("lute_thai"):
        pytest.skip("lute_thai parser plugin not installed")
    book = make_book(
        "Thai Grammar Demo",
        ["ผมกำลังอ่านหนังสือ คุณจะไปไหน เขาไม่ชอบกาแฟ"],
        thai,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "th_progressive" in keys
    assert "th_future" in keys
    assert "th_negation" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_arabic_grammar_analysis_uses_ar_engine(client, empty_db, arabic):
    "阿拉伯语书籍应走 pyarabic 语法引擎。"
    pytest.importorskip("pyarabic")
    book = make_book(
        "Arabic Grammar Demo",
        ["هل أنت هنا؟ كان الجو جميلاً. الكتاب على الطاولة."],
        arabic,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "ar_questions" in keys
    assert "ar_kana" in keys
    assert "ar_al" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def _get_or_create_language(name):
    "Fetch a predefined language from the db, creating it if needed."
    from lute.db import db as _db
    from lute.language.service import Service as LangService
    from lute.models.language import Language

    lang = _db.session.query(Language).filter(Language.name == name).first()
    if lang is None:
        lang = LangService(_db.session).get_language_def(name).language
        _db.session.add(lang)
        _db.session.commit()
    return lang


def test_grammar_analysis_fallback_for_other_languages(client, empty_db):
    "无专用引擎的语言（如土耳其语）应退回通用正则规则库。"
    lang = _get_or_create_language("Turkish")
    book = make_book(
        "Fallback Grammar Demo",
        ["If it rains, then we stay home."],
        lang,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    names = {g["name"] for g in data}
    assert "条件句 (Conditional)" in names
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_grammar_analysis_unknown_book_404(client, empty_db):
    "不存在的书应返回 404。"
    resp = client.get("/read/grammar_analysis/999999/1")
    assert resp.status_code == 404


def test_language_edit_shows_engine_status(client, empty_db, english):
    "语言编辑页应显示语法引擎状态行。"
    resp = client.get(f"/language/edit/{english.id}")
    assert resp.status_code == 200, resp.data
    body = resp.data.decode("utf-8")
    assert "grammar engine" in body
    assert "english" in body


def test_language_edit_without_engine_shows_note(client, empty_db):
    "无专属引擎的语言显示 basic rules 说明。"
    from lute.db import db as _db
    from lute.language.service import Service as LangService
    from lute.models.language import Language

    lang = _db.session.query(Language).filter(Language.name == "Turkish").first()
    if lang is None:
        lang = LangService(_db.session).get_language_def("Turkish").language
        _db.session.add(lang)
        _db.session.commit()
    resp = client.get(f"/language/edit/{lang.id}")
    assert resp.status_code == 200, resp.data
    assert "No dedicated grammar engine" in resp.data.decode("utf-8")


def test_language_edit_ja_ko_note_parser_shipped_engine(
    client, empty_db, korean, japanese
):
    "日语/韩语语言页不谎称没有引擎：说明引擎（Sudachi/Kiwi）随 parser 提供。"
    resp = client.get(f"/language/edit/{korean.id}")
    assert resp.status_code == 200, resp.data
    body = resp.data.decode("utf-8")
    assert "Kiwi" in body
    assert "No dedicated grammar engine" not in body
    assert "한국어" in body, "语法解释语言下拉应有 한국어 选项"

    resp = client.get(f"/language/edit/{japanese.id}")
    assert resp.status_code == 200, resp.data
    body = resp.data.decode("utf-8")
    assert "Sudachi" in body
    assert "No dedicated grammar engine" not in body


def test_install_route_rejects_unknown_extra(client, empty_db):
    "未知 extra 的安装请求应报错并不执行 pip。"
    resp = client.post(
        "/language/grammar_engine/install/klingon", follow_redirects=True
    )
    assert resp.status_code == 200
    assert "Unknown grammar engine" in resp.data.decode("utf-8")


def test_grammar_analysis_strips_zws_from_client_snippet(client, empty_db, korean):
    if not is_supported("lute_korean"):
        pytest.skip("lute_korean parser not installed")
    """
    阅读器把空段落渲染成零宽空格占位符，客户端拼接 snippet 时会把它们一起
    发回。韩语 Kiwi 分词器会把零宽空格单独切成 token，旧代码在词内嵌 zws
    时会把多词索引算到越界并抛 IndexError（书 44 的 500 就是这个），分析前
    必须清理掉。
    """
    zws = "\u200b"
    book = make_book(
        "ZWS Snippet Demo",
        ["저는 영화를 보고 있어요."],
        korean,
    )
    db.session.add(book)
    db.session.commit()

    # 模拟客户端发回的 snippet：空段落占位符 + 正文。
    snippet = f"\n  {zws}\n저는 영화를 보고 있어요.\n  {zws}\n"
    resp = client.get(
        f"/read/grammar_analysis/{book.id}/1", query_string={"text": snippet}
    )
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    names = {g["name"] for g in data}
    assert "-고 있다" in names
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_korean_decomposed_hangul_is_composed(client, empty_db, korean):
    if not is_supported("lute_korean"):
        pytest.skip("lute_korean parser not installed")
    """
    导入的书可能把音节的收音存成独立字母（"거세어지" + U+11AF 而不是
    "거세어질"），这是形态分析器留下的痕迹。Kiwi 会把 ㄹ/ㄴ 尾音当成独立
    token，返回的偏移落在音节内部，前端按偏移切句子就会把一个字切成两半
    （书 286 第 3 页的例句就是这个）。分析前必须先组合成 NFC。
    """
    broken = (
        "김 후보자를 임명하면서 동시에 공소취소 문제에도 원론적이\u11ab 답변을 내놓을 경우 야권의 공세는 더 거세어지\u11af 수 있다."
    )
    assert not unicodedata.is_normalized("NFC", broken)
    book = make_book("Korean Decomposed Demo", [broken], korean)
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    entry = next(g for g in data if g["key"] == "ko_su_issda")
    example = entry["examples"][0]
    sentence = example["sentence"]
    assert unicodedata.is_normalized("NFC", sentence), "返回的句子应是组合形式"
    assert "거세어질" in sentence
    assert example["matches"], "应给出高亮偏移"
    for m in example["matches"]:
        matched = sentence[m["start"] : m["end"]]
        assert not any(
            "\u1100" <= ch <= "\u11ff" for ch in matched
        ), f"高亮落在音节内部，会切字: {matched!r}"


def test_korean_grammar_analysis_ko_display_language(client, empty_db, korean):
    if not is_supported("lute_korean"):
        pytest.skip("lute_korean parser not installed")
    "grammar_translate_lang=ko 时，韩语语法点应返回韩语释义。"
    korean.grammar_translate_lang = "ko"
    db.session.add(korean)
    db.session.commit()
    book = make_book(
        "Korean Display Lang Demo",
        ["저는 지금 밥을 먹고 있어요."],
        korean,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    go_issda = next(g for g in data if g["key"] == "ko_go_issda")
    assert "진행" in go_issda["desc"]
    assert "is/am/are" not in go_issda["desc"]


def test_korean_panel_carries_the_formation_reference_and_notes(
    client, empty_db, korean
):
    if not is_supported("lute_korean"):
        pytest.skip("lute_korean parser not installed")
    """
    韩语面板应与日语面板一样给出 接续 / 参考例句（带高亮）/ 注意点。
    合并脚本以前把 formation 整个丢掉、把 notes 写死成空串，所以韩语面板
    只有一条释义。这里走真实路由，确认面板真的拿到这三块，并且 zh 面板里
    没有漏出未翻译的韩文或英文。
    """
    korean.grammar_translate_lang = "zh"
    db.session.add(korean)
    db.session.commit()
    sentence = "그 회사는 무리하게 확장한 나머지 재정적 위기를 맞게 되었다."
    book = make_book("Korean Panel Blocks Demo", [sentence], korean)
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    entry = next(g for g in data if g["key"] == "kgm_으나머지__282dc4")
    assert entry["formation"], "缺少接续"
    assert entry["notes"], "缺少注意点"
    # The block quotes the row's own curated example (the book's sentence),
    # not the page sentence that happened to match -- so what is pinned here
    # is that the highlight lands on the point inside it.
    ref = entry["reference"]
    assert ref["sentence"], "参考例句为空"
    assert ref["text"], "缺少参考例句译文"
    assert ref["matches"], "参考例句没有高亮偏移"
    highlighted = []
    for m in ref["matches"]:
        assert 0 <= m["start"] < m["end"] <= len(ref["sentence"])
        highlighted.append(ref["sentence"][m["start"] : m["end"]])
    assert any("나머지" in h for h in highlighted), highlighted

    # The panel's zh filter would hide any field carrying a Latin word, so a
    # regression here shows up as a silently missing block rather than as
    # English text on screen.
    for blob in (entry["desc"], entry["formation"], entry["notes"], ref["text"]):
        assert not re.search(r"[A-Za-z]{3,}", blob), blob
        assert re.search(r"[\u4e00-\u9fff]", blob), blob


def test_japanese_grammar_analysis_uses_ja_engine(client, empty_db, japanese):
    if not is_supported("japanese_sudachi"):
        pytest.skip("japanese_sudachi parser not installed")
    "日语书籍应走 Sudachi 语法引擎，返回 N5 规则与例句。"
    book = make_book(
        "Japanese Grammar Demo",
        ["日本に行きたいです。今、ご飯を食べています。"],
        japanese,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    names = {g["name"] for g in data}
    assert "〜たい" in names
    assert "〜ている" in names
    for g in data:
        assert g["level"] == "N5", "日语语法点都应标注 N5"
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_japanese_manga_grammar_analysis_reads_mokuro_ocr(client, empty_db, japanese):
    if not is_supported("japanese_sudachi"):
        pytest.skip("japanese_sudachi parser not installed")
    """
    Manga 书籍的页面 Text 为空，语法分析应从 mokuro OCR 数据中重建文本并
    返回日语语法点，而不是返回空列表。
    """
    from lute.book.model import Book
    from lute.book.service import Service as BookService

    pages = [
        {
            "version": "0.2.1",
            "img_path": "page.jpg",
            "img_width": 848,
            "img_height": 1264,
            "blocks": [
                {
                    "box": [10, 10, 100, 100],
                    "vertical": False,
                    "font_size": 25,
                    "lines": ["今日は学校に行きたいです。", "ご飯を食べています。"],
                },
            ],
        }
    ]

    book = Book()
    book.language_id = japanese.id
    book.title = "Manga Grammar Demo"
    book.book_type = "manga"
    book.manga_path = "manga/test-nonexistent"
    book.manga_data = json.dumps(
        {"version": "0.2.1", "pages": pages}, ensure_ascii=False
    )
    dbbook = BookService().import_book(book, db.session)

    resp = client.get(f"/read/grammar_analysis/{dbbook.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    assert data, "manga 页面通过 OCR 文本应能检出语法点"
    names = {g["name"] for g in data}
    assert "〜たい" in names
    assert "〜ている" in names
    for g in data:
        assert g["level"] == "N5", "日语语法点都应标注 N5"
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_mandarin_grammar_analysis_uses_zh_engine(client, empty_db, mandarin):
    if not is_supported("lute_mandarin"):
        pytest.skip("lute_mandarin parser not installed")
    "中文书籍应走零依赖中文语法引擎。"
    book = make_book(
        "Mandarin Grammar Demo",
        ["我吃了饭。他是昨天来的。他把作业写完了。"],
        mandarin,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "zh_le" in keys
    assert "zh_shi_de" in keys
    assert "zh_ba_sentence" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_cantonese_grammar_analysis_uses_yue_engine(client, empty_db):
    if not is_supported("lute_cantonese"):
        pytest.skip("lute_cantonese parser not installed")
    "粤语书籍应走零依赖粤语语法引擎。"
    from lute.models.language import Language

    lang = Language()
    lang.name = "Cantonese Chinese"
    lang.parser_type = "lute_cantonese"
    lang.character_substitutions = ""
    lang.regexp_split_sentences = ".!?。！？"
    lang.exceptions_split_sentences = ""
    lang.word_characters = "一-鿿"
    db.session.add(lang)
    db.session.commit()

    book = make_book(
        "Cantonese Grammar Demo",
        ["我食咗飯。佢唔去。佢睇緊電視。"],
        lang,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "yue_zo" in keys
    assert "yue_m" in keys
    assert "yue_gan" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_italian_grammar_analysis_uses_it_engine(client, empty_db):
    "意大利语书籍应走 spaCy 语法引擎。"
    pytest.importorskip("spacy")
    pytest.importorskip("it_core_news_sm")
    lang = _get_or_create_language("Italian")
    book = make_book(
        "Italian Grammar Demo",
        ["Ho mangiato ieri. Sto mangiando una pizza. Penso che sia giusto."],
        lang,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "it_passato_prossimo" in keys
    assert "it_stare_gerundio" in keys
    assert "it_congiuntivo" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"


def test_portuguese_grammar_analysis_uses_pt_engine(client, empty_db):
    "葡萄牙语书籍应走 spaCy 语法引擎。"
    pytest.importorskip("spacy")
    pytest.importorskip("pt_core_news_sm")
    lang = _get_or_create_language("Portuguese")
    book = make_book(
        "Portuguese Grammar Demo",
        ["Há um problema. Vou comer agora. Quando era criança, vivia aqui."],
        lang,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    keys = {g["key"] for g in data}
    assert "pt_haver" in keys
    assert "pt_ir_inf" in keys
    assert "pt_imperfeito" in keys
    for g in data:
        assert g["examples"], f"语法点 {g['name']} 缺少例句"
