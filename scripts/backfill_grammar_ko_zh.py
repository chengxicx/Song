"""Back-fill ``example_zh`` into the shipped Korean grammar library.

The ``example_en`` round gave every vendored kimchi row a curated 参考例句
block -- but only on the English panel: the Chinese panel hides the block
rather than print English under a Chinese heading, and production's Korean
language setting is ``grammar_translate_lang=zh``, so a reader on an ordinary
page still saw none.

This tops up an existing library **in place** with Chinese translations for
each row's first example (``scripts/grammar_materials_ko/zh_example_backfill.json``,
keyed by row key and carrying the Korean sentence it translates).  Only
``example_zh[0]`` is written, and only where it is missing -- the engine's
``_curated_reference`` prefers a zh-carrying example when choosing what to
quote, so one translated example per row is enough for every panel.

Guards: a row is skipped and reported when its first example no longer
matches the Korean sentence the translation was made from -- the pairing is
positional, and silently pairing a sentence with the wrong translation is
worse than leaving the row empty.

Usage::

    python -m scripts.backfill_grammar_ko_zh          # report
    python -m scripts.backfill_grammar_ko_zh --write  # apply
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_ko.json")
TRANSLATIONS = os.path.join(
    BASE, "scripts", "grammar_materials_ko", "zh_example_backfill.json"
)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def plan(library, translations):
    """
    Which rows get which translation.  Returns (fills, already, mismatched,
    uncovered).
    """
    fills = []
    already = 0
    mismatched = []
    uncovered = 0
    for row in library:
        examples = row.get("examples") or []
        if not any((e or "").strip() for e in examples):
            continue  # no examples at all -- nothing to translate
        if any((z or "").strip() for z in (row.get("example_zh") or [])):
            already += 1
            continue
        want = translations.get(row.get("key") or "")
        if want is None:
            uncovered += 1
            continue
        first = (examples[0] or "").strip()
        if first != want["ko"].strip():
            mismatched.append((row.get("key"), first, want["ko"]))
            continue
        fills.append((row, want["zh"]))
    return fills, already, mismatched, uncovered


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the changes")
    parser.add_argument("--library", default=LIBRARY)
    parser.add_argument("--translations", default=TRANSLATIONS)
    args = parser.parse_args(argv)

    library = load(args.library)
    translations = {r["key"]: r for r in load(args.translations)}

    fills, already, mismatched, uncovered = plan(library, translations)
    print(
        f"library rows with examples : {sum(1 for r in library if r.get('examples'))}"
    )
    print(f"rows to fill (index 0)     : {len(fills)}")
    print(f"rows already translated    : {already}")
    print(f"rows without a translation : {uncovered}")
    print(f"rows with a mismatch       : {len(mismatched)}")
    for key, got, expected in mismatched:
        print(
            f"    ! {key}: examples[0] drifted\n"
            f"        library : {got}\n"
            f"        expected: {expected}"
        )

    if not args.write:
        print("\n(report only; pass --write to apply)")
        return 1 if mismatched else 0

    for row, zh in fills:
        # Index 0 only; the remaining slots stay "" so the list stays
        # index-aligned with ``examples`` / ``example_en``.
        row["example_zh"] = [zh] + [""] * (len(row["examples"]) - 1)
    # indent=1 and no trailing newline, matching the generator and the
    # example_en backfill, so this shows up as a one-line-per-row diff.
    with open(args.library, "w", encoding="utf-8") as fh:
        json.dump(library, fh, ensure_ascii=False, indent=1)
    print(f"\nwrote {args.library}")
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
