"""Back-fill ``example_en`` into the shipped Korean grammar library.

The kimchi-grammar snapshot behind ``lute/jlpt_data/grammar_ko.json`` ships a
translation for every one of its example sentences, and
``generate_grammar_ko.py`` used to drop them.  Without a translation the
panel's folded 参考例句 block has nothing to quote, so the Korean panel showed
one for the merged material rows and for none of the 403 vendored ones -- which
is what a reader sees on an ordinary page, where every matched point is a
vendored row.

This tops up an existing library **in place** rather than regenerating it:
``generate_grammar_ko.py`` writes the whole file and would drop the merged
material rows and the zh/ko/formation/notes enrichment.

Only ``example_en`` is written, and only where it is missing.  A row whose
``examples`` no longer match the source is left alone and reported: the two
lists are index-aligned, so a mismatch would pair a sentence with the wrong
translation, and silently doing that is worse than not filling it.

Usage::

    python -m scripts.backfill_grammar_ko_examples <kimchi-point-dir>          # report
    python -m scripts.backfill_grammar_ko_examples <kimchi-point-dir> --write  # apply
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_ko.json")

sys.path.insert(0, BASE)

from lute.jlpt_data import (
    generate_grammar_ko,
)  # noqa: E402  pylint: disable=wrong-import-position


def load_library(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def plan(library, source_by_key):
    """
    Which rows get which translations.  Returns (fills, already, mismatched).
    """
    fills = []
    already = 0
    mismatched = []
    for row in library:
        key = row.get("key") or ""
        src = source_by_key.get(key)
        if src is None:
            continue  # a merged material row, or a row dropped from the source
        wanted = src.get("example_en") or []
        if not any(wanted):
            continue  # the source has no translations for this row either
        if any((e or "").strip() for e in (row.get("example_en") or [])):
            already += 1
            continue
        if (row.get("examples") or []) != (src.get("examples") or []):
            mismatched.append(key)
            continue
        fills.append((row, wanted))
    return fills, already, mismatched


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("point_dir", help="a kimchi-grammar checkout's point/ dir")
    parser.add_argument("--write", action="store_true", help="apply the changes")
    parser.add_argument("--library", default=LIBRARY)
    args = parser.parse_args(argv)

    source = generate_grammar_ko.generate(args.point_dir)
    source_by_key = {r["key"]: r for r in source}
    library = load_library(args.library)

    fills, already, mismatched = plan(library, source_by_key)
    n_examples = sum(
        len([e for e in wanted if (e or "").strip()]) for _, wanted in fills
    )

    print(f"library rows           : {len(library)}")
    print(f"source rows            : {len(source)}")
    print(f"rows to fill           : {len(fills)}  ({n_examples} example translations)")
    print(f"rows already filled    : {already}")
    print(f"rows with a mismatch   : {len(mismatched)}")
    for key in mismatched:
        print(f"    ! {key}: examples differ from the source; left alone")

    if not args.write:
        print("\n(report only; pass --write to apply)")
        return 0

    for row, wanted in fills:
        row["example_en"] = wanted
    # indent=1 and no trailing newline, matching the merge scripts and the
    # generator, so a back-fill shows up as a one-line-per-row diff.
    with open(args.library, "w", encoding="utf-8") as fh:
        json.dump(library, fh, ensure_ascii=False, indent=1)
    print(f"\nwrote {args.library}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
