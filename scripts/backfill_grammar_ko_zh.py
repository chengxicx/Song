"""Back-fill ``example_zh`` into the shipped Korean grammar library.

The ``example_en`` round gave every vendored kimchi row a curated 参考例句
block -- but only on the English panel: the Chinese panel hides the block
rather than print English under a Chinese heading, and production's Korean
language setting is ``grammar_translate_lang=zh``, so a reader on an ordinary
page still saw none.

Two materials feed this, in order:

  * ``scripts/grammar_materials_ko/zh_example_backfill.json`` -- keyed by row
    key and carrying the Korean sentence it translates.  It covered
    ``example_zh[0]`` for every row, which is enough for the panel: the
    engine's ``_curated_reference`` prefers a zh-carrying example when
    choosing what to quote.
  * ``scripts/grammar_materials_ko/zh_example_sentences*.json`` -- a flat
    ``{korean: chinese}`` map.  The library page shows *every* sentence a row
    ships, so the second and third examples were still bare Chinese-less
    Korean; this map fills any remaining slot, whichever index it sits at.

Both are guarded: a row is skipped and reported when its example no longer
matches the Korean sentence the translation was made from, and a sentence
with no entry is reported as uncovered -- silently pairing a sentence with
the wrong translation is worse than leaving the row empty.

Usage::

    python -m scripts.backfill_grammar_ko_zh          # report
    python -m scripts.backfill_grammar_ko_zh --write  # apply
"""

import argparse
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_ko.json")
TRANSLATIONS = os.path.join(
    BASE, "scripts", "grammar_materials_ko", "zh_example_backfill.json"
)
SENTENCES = os.path.join(
    BASE, "scripts", "grammar_materials_ko", "zh_example_sentences.json"
)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_sentences(path=SENTENCES):
    """
    The sentence-level material, merged from every ``zh_example_sentences*``
    file beside it.

    Keyed by the Korean sentence itself, so a translation can never land on
    the wrong sentence: a row whose examples were edited simply stops
    matching and is reported as uncovered.
    """
    folder = os.path.dirname(path)
    merged = {}
    for part in sorted(glob.glob(os.path.join(folder, "zh_example_sentences*.json"))):
        merged.update(load(part))
    return merged


def plan(library, translations, sentences):
    """
    Which slots get which translation.

    Returns ``(fills, already, mismatched, uncovered, slot_fills,
    slot_uncovered)``: ``fills`` are whole-row index-0 translations from the
    keyed material, ``slot_fills`` are ``(row, index, chinese)`` triples from
    the sentence map, and the two counters count the examples neither
    material can cover.
    """
    fills = []
    already = 0
    mismatched = []
    uncovered = 0
    slot_fills = []
    slot_uncovered = 0
    for row in library:
        examples = [(e or "").strip() for e in row.get("examples") or []]
        examples = [e for e in examples if e]
        if not examples:
            continue  # no examples at all -- nothing to translate
        slots = row.get("example_zh") or []
        if any((z or "").strip() for z in slots):
            already += 1
        else:
            want = translations.get(row.get("key") or "")
            if want is None:
                uncovered += 1
            elif examples[0] != want["ko"].strip():
                mismatched.append((row.get("key"), examples[0], want["ko"]))
            else:
                fills.append((row, want["zh"]))
        for index, sentence in enumerate(examples):
            if (slots[index] if index < len(slots) else "").strip():
                continue
            chinese = sentences.get(sentence)
            if chinese:
                slot_fills.append((row, index, chinese))
            else:
                slot_uncovered += 1
    return fills, already, mismatched, uncovered, slot_fills, slot_uncovered


def apply_fills(library, fills, slot_fills):
    "Write both kinds of fill into the library rows, in place."
    for row, zh in fills:
        # Index 0 only; the remaining slots stay "" so the list stays
        # index-aligned with ``examples`` / ``example_en``.
        row["example_zh"] = [zh] + [""] * (len(row["examples"]) - 1)
    for row, index, zh in slot_fills:
        slots = list(row.get("example_zh") or [])
        if len(slots) < len(row["examples"]):
            slots += [""] * (len(row["examples"]) - len(slots))
        slots[index] = zh
        row["example_zh"] = slots


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the changes")
    parser.add_argument("--library", default=LIBRARY)
    parser.add_argument("--translations", default=TRANSLATIONS)
    parser.add_argument("--sentences", default=SENTENCES)
    args = parser.parse_args(argv)

    library = load(args.library)
    translations = {r["key"]: r for r in load(args.translations)}
    sentences = load_sentences(args.sentences)

    fills, already, mismatched, uncovered, slot_fills, slot_uncovered = plan(
        library, translations, sentences
    )
    print(
        f"library rows with examples : {sum(1 for r in library if r.get('examples'))}"
    )
    print(f"rows to fill (index 0)     : {len(fills)}")
    print(f"rows already translated    : {already}")
    print(f"rows without a translation : {uncovered}")
    print(f"rows with a mismatch       : {len(mismatched)}")
    print(f"sentence material entries  : {len(sentences)}")
    print(f"example slots to fill      : {len(slot_fills)}")
    print(f"example slots uncovered    : {slot_uncovered}")
    for key, got, expected in mismatched:
        print(
            f"    ! {key}: examples[0] drifted\n"
            f"        library : {got}\n"
            f"        expected: {expected}"
        )

    if not args.write:
        print("\n(report only; pass --write to apply)")
        return 1 if mismatched or slot_uncovered else 0

    apply_fills(library, fills, slot_fills)
    # indent=1 and no trailing newline, matching the generator and the
    # example_en backfill, so this shows up as a one-line-per-row diff.
    with open(args.library, "w", encoding="utf-8") as fh:
        json.dump(library, fh, ensure_ascii=False, indent=1)
    print(f"\nwrote {args.library}")
    return 1 if mismatched or slot_uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
