"""
TEMPORARY diagnostic (delete after reading the CI log).

Collects what Term(text) actually does in the CI environment and fails
on purpose so that pytest prints the information.
"""

import sys

import jieba

from lute.models.term import Term
from lute_mandarin_parser.parser import MandarinParser


def test_000_debug(mandarin_chinese):
    "Diagnostics for the token_count failures on CI."
    MandarinParser.data_directory = None  # undo any stale test fixture
    lang = mandarin_chinese
    parser = lang.parser

    facts = [
        f"python={sys.version.split()[0]}",
        f"jieba={jieba.__version__}",
        f"cut_370b3d={list(jieba.cut('你说'))}",
        f"parser_type={lang.parser_type}",
        f"word_characters={lang.word_characters!r}",
        f"data_directory={parser.data_directory!r}",
        f"exceptions_map={parser._build_parser_exceptions_map()!r}",  # pylint: disable=protected-access
        f"raw_tokens={lang.get_parsed_tokens('你说')!r}",
    ]
    try:
        term = Term(lang, "你说")
        facts.append(
            f"term={term!r} count={term.token_count} text={term.text!r} "
            f"lc={term.text_lc!r}"
        )
    except Exception as exc:  # pylint: disable=broad-except
        facts.append(f"term RAISED {type(exc).__name__}: {exc}")

    raise AssertionError("DIAG " + " | ".join(facts))
