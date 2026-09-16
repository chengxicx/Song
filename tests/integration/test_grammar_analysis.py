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