"""Measure the Japanese grammar panel against a corpus of real pages.

Engine changes here are judged on two things the unit tests cannot show: how
many rows the panel lands on for a real page, and which rows those are.  Both
move whenever the derivation changes, and a change that fixes one entry can add
a row to most of the corpus -- stripping the parentheticals in
``_jp_fragments`` before splitting on ``+`` looks like a one-line improvement,
and it is, until ``お + V ます-stem + する (or ご + ... + する)`` starts
contributing a row for every する on 91.5% of pages.  Numbers like that are why
this exists: take them before and after, on the same corpus.

A corpus is either a Lute SQLite database -- the natural source, the pages the
reader actually reads -- or a dump of pages saved earlier, one per line,
hex-encoded by default because that is how the dumps in this project's notes
are stored.  Keep a dump out of the repository: those pages are book text.

Usage::

    python -m scripts.measure_grammar_panel --db /opt/lute/lute_data/users/chengxi/lute.db
    python -m scripts.measure_grammar_panel --corpus /tmp/ja_corpus.hex
    python -m scripts.measure_grammar_panel --corpus /tmp/pages.txt --plain
    python -m scripts.measure_grammar_panel --db lute.db --keys ds_nai-de-without-doing
    python -m scripts.measure_grammar_panel --db lute.db --json > /tmp/after.json

``--json`` prints the same numbers machine-readably, so two runs diff with a
one-liner instead of the eye.

Sampling a database is the first ``--limit`` pages of the matching books in
book/page order -- deterministic, so two runs compare like with like.
"""

import argparse
import binascii
import collections
import json
import os
import sqlite3
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lute.read.render import grammar_analysis_ja as G  # noqa: E402

# Lute marks term boundaries with a zero-width space.  It is not text and the
# panel would see it as part of a word, so drop it on the way in.
_ZWS = "\u200b"

# Aggregated rows: one entry each, holding many sentences, so they are always
# the highest-frequency "rows" and would crowd out the real ones.
_AGGREGATE_KEYS = ("basic_forms", "basic_particles")

_PAGES_SQL = """
SELECT t.TxText
  FROM texts t
  JOIN books b ON b.BkID = t.TxBkID
  JOIN languages l ON l.LgID = b.BkLgID
 WHERE l.LgName = ?
   AND (b.BkBookType IS NULL OR b.BkBookType = '')
   AND t.TxText IS NOT NULL
   AND t.TxText <> ''
 ORDER BY b.BkID, t.TxOrder
 LIMIT ?
"""


def _clean(text):
    return (text or "").replace(_ZWS, "")


def load_corpus(path, plain=False):
    "Pages from a dump: one page per line, hex-encoded unless ``plain``."
    pages = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if not plain:
                line = binascii.unhexlify(line).decode("utf-8")
            pages.append(_clean(line))
    return pages


def load_db(path, language="Japanese", limit=400):
    "Pages from a Lute database: prose books only, in book/page order."
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        rows = conn.execute(_PAGES_SQL, (language, limit)).fetchall()
    return [_clean(r[0]) for r in rows]


def _stats(values):
    ordered = sorted(values)
    return {
        "median": statistics.median(ordered),
        "mean": statistics.mean(ordered),
        "p90": ordered[int(0.9 * len(ordered)) - 1],
        "max": max(ordered),
    }


def measure(pages):
    """Panel state over a corpus: per-page counts, per-rule page share."""
    per_rule = collections.Counter()
    counts = []
    sizes = []
    dup_pages = 0
    for page in pages:
        entries = G.analyze_japanese(page, display_lang="zh")
        counts.append(len(entries))
        sizes.append(len(json.dumps(entries, ensure_ascii=False).encode("utf-8")))
        if len(entries) != len({e["name"] for e in entries}):
            dup_pages += 1
        for entry in entries:
            per_rule[entry["key"]] += 1
    total = max(len(pages), 1)
    return {
        "pages": len(pages),
        "entries": sum(counts),
        "entries_per_page": _stats(counts),
        "response_bytes": _stats(sizes),
        "duplicate_name_pages_pct": 100.0 * dup_pages / total,
        "page_share": {k: 100.0 * v / total for k, v in per_rule.most_common()},
    }


def report(state, top, keys):
    "Human-readable panel state."
    per_page = state["entries_per_page"]
    size = state["response_bytes"]
    print("=== 面板状态（%d 页）===" % state["pages"])
    print(
        "  条目/页  中位 %.1f  均值 %.1f  p90 %d  最大 %d"
        % (per_page["median"], per_page["mean"], per_page["p90"], per_page["max"])
    )
    print(
        "  响应体   中位 %dB  p90 %dB  最大 %dB"
        % (size["median"], size["p90"], size["max"])
    )
    print(
        "  重名行的页 %.1f%%  条目总数 %d"
        % (state["duplicate_name_pages_pct"], state["entries"])
    )

    index = {r["key"]: r for r in G._ALL_RULES}
    share = state["page_share"]

    if keys:
        print()
        print("=== 指定条目 ===")
        for key in [k.strip() for k in keys.split(",") if k.strip()]:
            rule = index.get(key)
            print(
                "  %6.1f%%  %-26s %s"
                % (share.get(key, 0.0), (rule or {}).get("pattern", "?")[:26], key)
            )

    print()
    print("=== 最高频 %d 行（占页 %% ／ 级别 ／ 面板名 ／ key）===" % top)
    shown = 0
    for key, pct in share.items():
        if key in _AGGREGATE_KEYS:
            continue
        rule = index.get(key)
        print(
            "  %6.1f%%  [%-2s] %-26s %s"
            % (pct, (rule or {}).get("level", "?"), (rule or {}).get("pattern", key)[:26], key)
        )
        shown += 1
        if shown >= top:
            break
    for key in _AGGREGATE_KEYS:
        if key in share:
            print("  %6.1f%%  聚合行 %s" % (share[key], key))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--corpus", help="page dump, one page per line")
    src.add_argument("--db", help="Lute SQLite database to read pages from")
    ap.add_argument("--plain", action="store_true", help="--corpus is plain text, not hex")
    ap.add_argument("--language", default="Japanese", help="--db language name")
    ap.add_argument("--limit", type=int, default=400, help="--db page limit")
    ap.add_argument("--top", type=int, default=15, help="rows to list by page share")
    ap.add_argument("--keys", help="comma-separated rule keys to report individually")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    pages = (
        load_corpus(args.corpus, plain=args.plain)
        if args.corpus
        else load_db(args.db, args.language, args.limit)
    )
    if not pages:
        print("no pages found", file=sys.stderr)
        return 1

    state = measure(pages)
    if args.json:
        json.dump(state, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        report(state, args.top, args.keys)
    return 0


if __name__ == "__main__":
    sys.exit(main())
