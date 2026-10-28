"""
TEMPORARY diagnostic: why does Term(text).token_count come out 1 on CI.

Delete this file once the log has been read.
"""

import sys

import jieba

from lute.models.term import Term
from lute.parse.registry import supported_parser_types


def test_zz_debug(mandarin_chinese):
    "Print what the CI environment actually produces."
    lang = mandarin_chinese
    print("\n==DEBUG python", sys.version)
    print("==DEBUG jieba", jieba.__version__, list(jieba.cut("你说")))
    print("==DEBUG parser_type", lang.parser_type, "supported", supported_parser_types())
    print("==DEBUG word_characters", repr(lang.word_characters))
    try:
        parser = lang.parser
        print("==DEBUG parser", parser)
    except Exception as exc:  # pylint: disable=broad-except
        print("==DEBUG parser RAISED", type(exc).__name__, exc)
        return
    print("==DEBUG data_directory", getattr(parser, "data_directory", "n/a"))
    raw = lang.get_parsed_tokens("你说")
    print("==DEBUG raw tokens", raw)
    t = Term(lang, "你说")
    print("==DEBUG term", repr(t), "count", t.token_count, "text", repr(t.text))
