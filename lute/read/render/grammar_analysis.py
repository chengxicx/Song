"""
可行性原型：根据当前阅读页的句子，用正则规则库识别常见英文语法点。

说明：
- 这是最小 demo，规则覆盖有限，不追求完备。
- 数据来源：阅读页已经解析好的句子文本。
- 每条规则给出：语法名、简短说明，以及命中的原文例句。
"""

import re


# Japanese is identified by its configured parser type (MeCab or Sudachi)
# or by its language name.
_JAPANESE_PARSER_TYPES = {"japanese", "japanese_sudachi"}


def is_japanese_language(language):
    """
    True if the given Language is a Japanese language whose reading page
    should use the Sudachi-based grammar engine.
    """
    if language is None:
        return False
    parser_type = (getattr(language, "parser_type", None) or "").strip().lower()
    if parser_type in _JAPANESE_PARSER_TYPES:
        return True
    name = (getattr(language, "name", None) or "").lower()
    return "japanese" in name or "日本語" in name


# 每条规则：{"key", "name", "desc", "pattern"}
# pattern 用正则在句子文本上匹配（忽略大小写、多行不要求）。
_GRAMMAR_RULES = [
    {
        "name": "条件句 (Conditional)",
        "desc": "if ... then ... 表达条件关系",
        "pattern": re.compile(r"\bif\b\s+.{0,60}?,?\s+\bthen\b", re.IGNORECASE),
    },
    {
        "name": "并列强调 (not only ... but also)",
        "desc": "强调两者，语气更强",
        "pattern": re.compile(
            r"\bnot\s+only\b.{0,60}?\bbut\s+also\b", re.IGNORECASE
        ),
    },
    {
        "name": "太...而不能 (too ... to)",
        "desc": "too + adj + to do",
        "pattern": re.compile(r"\btoo\s+\w+\s+to\s+\w+", re.IGNORECASE),
    },
    {
        "name": "如此...以至于 (so ... that)",
        "desc": "so + adj + that 引导结果从句",
        "pattern": re.compile(r"\bso\s+\w+\s+that\b", re.IGNORECASE),
    },
    {
        "name": "选择连词 (either ... or)",
        "desc": "二选一",
        "pattern": re.compile(r"\beither\b.{0,40}?\bor\b", re.IGNORECASE),
    },
    {
        "name": "否定并列 (neither ... nor)",
        "desc": "两者都不",
        "pattern": re.compile(r"\bneither\b.{0,40}?\bnor\b", re.IGNORECASE),
    },
    {
        "name": "同级比较 (as ... as)",
        "desc": "和...一样",
        "pattern": re.compile(r"\bas\s+\w+\s+as\s+\w+", re.IGNORECASE),
    },
    {
        "name": "比较级 (more ... than)",
        "desc": "比...更",
        "pattern": re.compile(r"\bmore\b\s+.{1,40}?\bthan\b", re.IGNORECASE),
    },
    {
        "name": "过去常常 (used to)",
        "desc": "过去（曾经）的习惯/状态",
        "pattern": re.compile(r"\bused\s+to\s+\w+", re.IGNORECASE),
    },
    {
        "name": "打算做 (be going to)",
        "desc": "表示计划/将要",
        "pattern": re.compile(r"\b(is|are|am|was|were)\s+going\s+to\b", re.IGNORECASE),
    },
]


def analyze(sentences):
    """
    输入：句子文本列表（list[str]）。
    输出：list[dict]，每个命中的语法点一项：
      {"name", "desc", "examples": [{"sentence", "matches"}]}
    同一语法点在一页出现多次时合并，例句去重保留原文。
    "matches" 是例句中命中的原文片段，用于前端在阅读文本中高亮。
    """
    matched = []
    for sentence in sentences:
        if not sentence or not sentence.strip():
            continue
        for rule in _GRAMMAR_RULES:
            m = rule["pattern"].search(sentence)
            if not m:
                continue
            # 找到已记录的同名语法点，追加例句；否则新建。
            entry = next((e for e in matched if e["name"] == rule["name"]), None)
            if entry is None:
                entry = {
                    "name": rule["name"],
                    "desc": rule["desc"],
                    "examples": [],
                }
                matched.append(entry)
            if sentence not in [ex["sentence"] for ex in entry["examples"]]:
                entry["examples"].append(
                    {"sentence": sentence, "matches": [{"start": m.start(), "end": m.end()}]}
                )
    return matched