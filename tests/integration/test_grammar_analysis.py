"""
可行性原型：/read/grammar_analysis 接口的冒烟测试。

English/Spanish/Russian books route to their dedicated spaCy / pymorphy3
engines (skipped when the optional dependency isn't installed); every
other language falls back to the generic regex rule library.
"""

import json
import pytest
from lute.db import db
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


def test_grammar_analysis_fallback_for_other_languages(client, empty_db, french):
    "无专用引擎的语言（如法语）应退回通用正则规则库。"
    book = make_book(
        "Fallback Grammar Demo",
        ["If it rains, then we stay home."],
        french,
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


def test_grammar_analysis_strips_zws_from_client_snippet(client, empty_db, korean):
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


def test_japanese_grammar_analysis_uses_ja_engine(client, empty_db, japanese):
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


def test_japanese_manga_grammar_analysis_reads_mokuro_ocr(
    client, empty_db, japanese
):
    """
    Manga 书籍的页面 Text 为空，语法分析应从 mokuro OCR 数据中重建文本并
    返回日语语法点，而不是返回空列表。
    """
    from lute.book.model import Book
    from lute.book.service import Service as BookService

    pages = [{
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
    }]

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