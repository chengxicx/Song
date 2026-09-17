"""
可行性原型：/read/grammar_analysis 接口的冒烟测试。
"""

import json
from lute.db import db
from tests.utils import make_book


def test_grammar_analysis_returns_points(client, empty_db, english):
    "创建一本含语法例句的书，调用接口应返回语法点与例句。"
    book = make_book(
        "Grammar Demo",
        [
            "If it rains, then we stay home. The box is too heavy to lift. "
            "Neither he nor she likes it."
        ],
        english,
    )
    db.session.add(book)
    db.session.commit()

    resp = client.get(f"/read/grammar_analysis/{book.id}/1")
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data.decode("utf-8"))
    names = {g["name"] for g in data}
    assert "条件句 (Conditional)" in names
    assert "太...而不能 (too ... to)" in names
    assert "否定并列 (neither ... nor)" in names
    # 每个语法点都应带原文例句。
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