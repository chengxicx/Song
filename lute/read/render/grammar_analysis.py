"""
可行性原型：根据当前阅读页的句子，用正则规则库识别常见英文语法点。

说明：
- 这是最小 demo，规则覆盖有限，不追求完备。
- 数据来源：阅读页已经解析好的句子文本。
- 每条规则给出：语法名、简短说明，以及命中的原文例句。

This module also hosts the per-language dispatch helpers that steer a
grammar-analysis request to the right engine, and the startup check that
reports missing optional engine dependencies.
"""

import importlib.util
import logging
import re
import subprocess
import sys


# Japanese is identified by its configured parser type (MeCab or Sudachi)
# or by its language name.
_JAPANESE_PARSER_TYPES = {"japanese", "japanese_sudachi"}

# Korean parser types registered by the lute-korean plugin.
_KOREAN_PARSER_TYPES = {"korean", "lute_korean"}

# Thai parser types registered by the lute-thai plugin.
_THAI_PARSER_TYPES = {"thai", "lute_thai"}


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


def is_korean_language(language):
    """
    True if the given Language is a Korean language whose reading page
    should use the Kiwi-based grammar engine.
    """
    if language is None:
        return False
    parser_type = (getattr(language, "parser_type", None) or "").strip().lower()
    if parser_type in _KOREAN_PARSER_TYPES:
        return True
    name = (getattr(language, "name", None) or "").lower()
    return "korean" in name or "한국어" in name or "韩语" in name or "韓語" in name


# English / Spanish / Russian are identified by language name only: they all
# share the generic "spacedel" parser with many other languages, so there is
# no parser_type to key on.


def is_mandarin_chinese_language(language):
    "True if the given Language should use the Mandarin grammar engine."
    if language is None:
        return False
    parser_type = (getattr(language, "parser_type", None) or "").strip().lower()
    if parser_type == "lute_mandarin":
        return True
    if parser_type == "lute_cantonese":
        return False
    name = (getattr(language, "name", None) or "").lower()
    if "classical" in name or "文言" in name:
        return False
    if (
        "cantonese" in name
        or "粤语" in name
        or "粵語" in name
        or "广东话" in name
        or "廣東話" in name
    ):
        # "Cantonese Chinese" contains "chinese"; keep the two apart.
        return False
    return (
        "mandarin" in name
        or "chinese" in name
        or "中文" in name
        or "汉语" in name
        or "漢語" in name
        or "普通话" in name
    )


def is_cantonese_language(language):
    "True if the given Language should use the Cantonese grammar engine."
    if language is None:
        return False
    parser_type = (getattr(language, "parser_type", None) or "").strip().lower()
    if parser_type == "lute_cantonese":
        return True
    name = (getattr(language, "name", None) or "").lower()
    return (
        "cantonese" in name
        or "粤语" in name
        or "粵語" in name
        or "广东话" in name
        or "廣東話" in name
    )


def is_english_language(language):
    "True if the given Language should use the spaCy English grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "english" in name or "英语" in name or "英文" in name


def is_spanish_language(language):
    "True if the given Language should use the spaCy Spanish grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return (
        "spanish" in name
        or "español" in name
        or "espanol" in name
        or "西班牙语" in name
        or "西语" in name
    )


def is_russian_language(language):
    "True if the given Language should use the pymorphy3 Russian grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "russian" in name or "русский" in name or "俄语" in name or "俄文" in name


def is_french_language(language):
    "True if the given Language should use the spaCy French grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "french" in name or "français" in name or "francais" in name or "法语" in name or "法文" in name or "法語" in name


def is_german_language(language):
    "True if the given Language should use the spaCy German grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "german" in name or "deutsch" in name or "德语" in name or "德文" in name or "德語" in name


def is_thai_language(language):
    "True if the given Language should use the pythainlp Thai grammar engine."
    if language is None:
        return False
    parser_type = (getattr(language, "parser_type", None) or "").strip().lower()
    if parser_type in _THAI_PARSER_TYPES:
        return True
    name = (getattr(language, "name", None) or "").lower()
    return "thai" in name or "ไทย" in name or "泰语" in name or "泰文" in name or "泰語" in name


def is_arabic_language(language):
    "True if the given Language should use the pyarabic Arabic grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "arabic" in name or "العربية" in name or "阿拉伯语" in name or "阿拉伯文" in name or "阿拉伯語" in name


def is_italian_language(language):
    "True if the given Language should use the spaCy Italian grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "italian" in name or "italiano" in name or "意大利语" in name or "意大利文" in name or "意大利語" in name


def is_portuguese_language(language):
    "True if the given Language should use the spaCy Portuguese grammar engine."
    if language is None:
        return False
    name = (getattr(language, "name", None) or "").lower()
    return "portuguese" in name or "português" in name or "portugues" in name or "葡萄牙语" in name or "葡萄牙文" in name or "葡萄牙語" in name


# ---- startup dependency check -----------------------------------------
#
# language label, detector, importable deps, pip extra that provides them
_ENGINE_REQUIREMENTS = [
    ("Mandarin Chinese", is_mandarin_chinese_language, (), "chinese"),
    ("Cantonese", is_cantonese_language, (), "cantonese"),
    ("English", is_english_language, ("spacy", "en_core_web_sm"), "english"),
    ("Spanish", is_spanish_language, ("spacy", "es_core_news_sm"), "spanish"),
    ("Russian", is_russian_language, ("pymorphy3",), "russian"),
    ("French", is_french_language, ("spacy", "fr_core_news_sm"), "french"),
    ("German", is_german_language, ("spacy", "de_core_news_sm"), "german"),
    ("Italian", is_italian_language, ("spacy", "it_core_news_sm"), "italian"),
    ("Portuguese", is_portuguese_language, ("spacy", "pt_core_news_sm"), "portuguese"),
    ("Thai", is_thai_language, ("pythainlp",), "thai"),
    ("Arabic", is_arabic_language, ("pyarabic",), "arabic"),
]

# Concrete pip requirements providing each engine, mirroring the
# optional-dependencies extras in pyproject.toml (kept in sync by hand).
# The language page's Install button pip-installs these directly, which
# works for editable checkouts and PyPI installs alike.
_ENGINE_INSTALL_SPECS = {
    "english": [
        "spacy>=3.8.0,<3.8.4",
        "en-core-web-sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl",
    ],
    "spanish": [
        "spacy>=3.8.0,<3.8.4",
        "es-core-news-sm@https://github.com/explosion/spacy-models/releases/download/es_core_news_sm-3.8.0/es_core_news_sm-3.8.0-py3-none-any.whl",
    ],
    "french": [
        "spacy>=3.8.0,<3.8.4",
        "fr-core-news-sm@https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.8.0/fr_core_news_sm-3.8.0-py3-none-any.whl",
    ],
    "german": [
        "spacy>=3.8.0,<3.8.4",
        "de-core-news-sm@https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl",
    ],
    "russian": ["pymorphy3>=2.0,<3", "pymorphy3-dicts-ru>=2.4,<3"],
    "italian": [
        "spacy>=3.8.0,<3.8.4",
        "it-core-news-sm@https://github.com/explosion/spacy-models/releases/download/it_core_news_sm-3.8.0/it_core_news_sm-3.8.0-py3-none-any.whl",
    ],
    "portuguese": [
        "spacy>=3.8.0,<3.8.4",
        "pt-core-news-sm@https://github.com/explosion/spacy-models/releases/download/pt_core_news_sm-3.8.0/pt_core_news_sm-3.8.0-py3-none-any.whl",
    ],
    "thai": ["pythainlp>=5.0,<6"],
    "arabic": ["pyarabic>=0.6,<2"],
}

_PIP_TIMEOUT_SECONDS = 900


def grammar_engine_for(language):
    """
    Return (label, extra) of the dedicated grammar engine serving the
    given language, or (None, None) when only the basic rules apply.
    """
    for label, detect, _deps, extra in _ENGINE_REQUIREMENTS:
        if detect(language):
            return label, extra
    return None, None


def grammar_engine_status(language):
    """
    UI summary of the grammar engine for one language:
      {"label", "extra", "installed", "missing", "installable"}
    label is None when the language has no dedicated engine.
    """
    label, extra = grammar_engine_for(language)
    if label is None:
        return {"label": None, "extra": None, "installed": False, "missing": [], "installable": False}
    deps = next(_deps for _l, _d, _deps, _e in _ENGINE_REQUIREMENTS if _e == extra)
    missing = [dep for dep in deps if importlib.util.find_spec(dep) is None]
    return {
        "label": label,
        "extra": extra,
        "installed": not missing,
        "missing": missing,
        "installable": extra in _ENGINE_INSTALL_SPECS,
    }


def install_grammar_engine(extra):
    """
    pip-install the packages providing one engine's extra.

    Returns (ok, message).  Mirrors the parser-plugin installer: a running
    app can usually import the new packages without a restart, but a
    restart is mentioned if the panel still shows the basic rules.
    """
    specs = _ENGINE_INSTALL_SPECS.get(extra or "")
    if not specs:
        return False, f"Unknown grammar engine '{extra}'"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", *specs],
            capture_output=True,
            text=True,
            timeout=_PIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip install of the {extra} engine timed out"
    except OSError as e:
        return False, f"Could not run pip: {e}"
    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return False, f"pip install of the {extra} engine failed:\n{output.strip()[-2000:]}"
    return True, (
        f"Installed the {extra} grammar engine. "
        "If the grammar panel still shows the basic rules, restart the app."
    )


def report_grammar_engine_status(session):
    """
    Startup check: when the DB has an English/Spanish/Russian language but
    its grammar-analysis dependency isn't installed, log the pip command
    that installs it.

    Books keep working either way: the grammar route falls back to the
    generic regex rule library below.  Uses importlib.util.find_spec so no
    heavy package is actually imported at startup.
    """
    # Imported here (not at module top) to avoid a circular import.
    from lute.models.language import Language

    names_by_extra = {}
    for lang in session.query(Language).all():
        for _label, detect, _deps, extra in _ENGINE_REQUIREMENTS:
            if detect(lang):
                names_by_extra.setdefault(extra, set()).add(lang.name)
    logger = logging.getLogger(__name__)
    for label, _detect, deps, extra in _ENGINE_REQUIREMENTS:
        names = names_by_extra.get(extra)
        if not names:
            continue
        missing = [dep for dep in deps if importlib.util.find_spec(dep) is None]
        if missing:
            logger.warning(
                "Grammar analysis for %s (%s) needs missing package(s): %s.  "
                'Run: pip install -e ".[%s]"; until then the grammar panel '
                "uses the basic regex rules.",
                ", ".join(sorted(names)),
                label,
                ", ".join(missing),
                extra,
            )


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