"""Triage the Japanese library's reference-only rows.

A row is *reference-only* when the engine derived no match spec for it: it
is listed on the /grammar page, marked "reference only", but the reader
never flags it in a book.  Some of that is deliberate -- a vocabulary row,
a conjugation table, a duplicate of another row -- and some is a genuine
gap (a construction the derivation cannot express).

This script prints the breakdown so the gap can be worked instead of
guessed, and names the rows a reviewed ``_SLOT_SPECS`` entry could recover.
It is the tool behind the batch at the end of ``_SLOT_SPECS``.

Usage::

    python -m scripts.screen_ja_reference_only
    python -m scripts.screen_ja_reference_only --show-recoverable
"""

import argparse
import collections
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from lute.read.render import grammar_analysis_ja as G  # noqa: E402

# Reviewed skip lists, in the order _load_level consults them.
_REASONS = [
    ("duplicate", "_DUPLICATE_IDS"),
    ("vocabulary", "_VOCAB_IDS"),
    ("concept", "_CONCEPT_IDS"),
    ("superseded", "_SUPERSEDED_IDS"),
]


def classify(rule):
    "Why a rule is reference-only."
    entry_id = rule.get("entry_id") or ""
    for reason, attribute in _REASONS:
        if entry_id in getattr(G, attribute):
            return reason
    return "no-pattern"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show-recoverable",
        action="store_true",
        help="list the no-pattern rows, with their pattern text",
    )
    args = parser.parse_args()

    rules = G._get_data_rules()  # pylint: disable=protected-access
    skipped = [rule for rule in rules if rule.get("skipped")]
    counts = collections.Counter(classify(rule) for rule in skipped)
    by_level = collections.Counter(rule["level"] for rule in skipped)

    print(f"{len(skipped)} of {len(rules)} data rows are reference-only\n")
    for reason, count in counts.most_common():
        print(f"  {reason:12} {count:4}")
    print(
        "\nby level: " + ", ".join(f"{lvl} {n}" for lvl, n in sorted(by_level.items()))
    )

    recoverable = [rule for rule in skipped if classify(rule) == "no-pattern"]
    print(f"\nno-pattern rows: {len(recoverable)}")
    print(
        "  of these, "
        f"{sum(1 for r in recoverable if r.get('entry_id') in G._SLOT_SPECS)}"  # pylint: disable=protected-access
        " already have a reviewed _SLOT_SPECS entry"
    )
    if args.show_recoverable:
        for rule in recoverable:
            print(f"  {rule['level']}  {rule['key']:44} {rule['pattern'][:60]}")


if __name__ == "__main__":
    main()
