"""
Dry run for "attach the lemma to the book-open term path" (roadmap 2.3).

Read-only over a Lute database.  Answers the questions that decide whether the
parent/lemma path is worth a round of work:

  * how many terms would get a parent at all, and how many of those are
    still status 0 (i.e. the user-visible payoff);
  * how many of those parents do not exist as terms yet (enabling this
    *creates* terms, which changes the term list and the stats);
  * how many lemmas are concatenations of several content words
    (読んでいます -> 読む + いる), the shape that needs a rule, not a
    straight copy of ``Parser.get_lemma()``.

The numbers to beat, measured on the production database 2026-09-20:
8119 ja terms, 1414 (17.4%) all-hiragana and therefore lemma-less by
construction, 257 terms (3.2%) carry a parent today -- all of them
hand-made, as the term form and the bulk-edit form are the only writers.

Usage::

    python -m scripts.probe_term_parents --db /opt/lute/.../lute.db
    python -m scripts.probe_term_parents --db /tmp/copy.db --langid 13 --samples 10
"""

import argparse
import collections
import random
import re
import sqlite3
import sys

from lute.parse.sudachi_parser import JapaneseSudachiParser

KANA_ONLY = re.compile(r"^[ぁ-んー]+$")
COMBINING = re.compile(r"[\u0300-\u036f]{2,}")


def main():
    args = _parse_args()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = db.execute(
        "select WoID, WoText, WoStatus from words where WoLgID = ?", (args.langid,)
    ).fetchall()
    if not rows:
        print(f"no terms for language {args.langid}")
        return
    existing = {(text or "").lower() for _id, text, _status in rows}

    parser = JapaneseSudachiParser()
    tokenizer = parser._build_tokenizer(parser._get_dict_setting())
    split_mode = parser._get_split_mode(parser._get_mode_setting())

    kana = []
    garbled = []
    linked = []
    multi = []
    new_parents = collections.Counter()

    for wo_id, text, status in rows:
        text = text or ""
        if COMBINING.search(text):
            garbled.append((wo_id, text, status))
        if KANA_ONLY.match(text):
            kana.append((wo_id, text, status))
            continue
        lemma = parser.get_lemma(text)
        if not lemma or lemma == text:
            continue
        kept = _content_tokens(parser, tokenizer, split_mode, text)
        linked.append((text, lemma, status))
        if len(kept) > 1:
            multi.append((text, lemma))
        if lemma.lower() not in existing:
            new_parents[lemma] += 1

    total = len(rows)
    pct = lambda n: f"{n / total * 100:.1f}%"  # noqa: E731
    print(f"terms (langid {args.langid})      : {total}")
    print(f"  all-hiragana, lemma-less      : {len(kana)} ({pct(len(kana))})")
    print(f"  would get a parent            : {len(linked)} ({pct(len(linked))})")
    print(
        f"    of those, status 0          : {sum(1 for _t, _l, s in linked if s == 0)}"
    )
    print(
        f"    multi-token (concatenated)  : {len(multi)} ({len(multi) / max(len(linked), 1) * 100:.1f}% of linked)"
    )
    print(f"  parent terms that don't exist : {len(new_parents)} distinct texts")
    print(f"    term rows this would add    : {sum(new_parents.values())} at most")
    if garbled:
        print(f"  [aside] terms with combining marks (garbled?): {len(garbled)}")

    random.seed(args.seed)
    if multi:
        print("\nmulti-token lemmas (need a rule, not a copy):")
        for text, lemma in random.sample(multi, min(args.samples, len(multi))):
            print(f"  {_vis(text):16s} -> {lemma}")
    print("\nstraightforward links (sample):")
    for text, lemma, status in random.sample(linked, min(args.samples, len(linked))):
        print(f"  {_vis(text):16s} -> {lemma:14s} status={status}")
    print("\nwould-be links by status:")
    for status, count in sorted(collections.Counter(s for _t, _l, s in linked).items()):
        print(f"  status={status}: {count}")


def _content_tokens(parser, tokenizer, split_mode, text):
    "Same walk as SudachiParser.get_lemma, returning the kept tokens."
    kept = []
    for m in tokenizer.tokenize(text.replace("\u200B", ""), mode=split_mode):
        surface = m.surface()
        if surface == "" or surface.strip() == "":
            continue
        pos = m.part_of_speech()
        if pos and pos[0] in parser._BOUND_POS1:
            continue
        kept.append(surface)
    return kept


def _vis(text):
    "Make zero-width spaces visible in the report."
    return text.replace("\u200B", "\u00B7")


def _parse_args():
    p = argparse.ArgumentParser(description="Probe term/lemma (parent) coverage.")
    p.add_argument(
        "--db", required=True, help="Lute SQLite database (opened read-only)."
    )
    p.add_argument("--langid", type=int, default=13, help="Language id to probe.")
    p.add_argument(
        "--samples", type=int, default=10, help="Rows to sample per section."
    )
    p.add_argument(
        "--seed", type=int, default=3, help="Sampling seed, for stable reports."
    )
    argv = sys.argv[1:]
    return p.parse_args(argv)


if __name__ == "__main__":
    main()
