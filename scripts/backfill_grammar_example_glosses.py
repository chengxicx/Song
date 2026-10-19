"""Back-fill ``example_en`` / ``example_ko`` into the shipped grammar corpora.

The Spanish, French, German and Russian corpora carry a Chinese gloss for
every example (``example_zh``, aligned positionally with ``examples``) but
no English or Korean one, so a reader on the default display language saw
the example sentence bare -- the golden sets only cover the hand-written
rules.  This tops the corpora up from the authored material under
``scripts/grammar_materials_<lang>/example_en.json`` (and ``example_ko.json``
once it exists).

The material is ``{row key: [[source sentence, translation], ...]}``.  The
source sentence is carried so the pairing can be checked: the corpus stores
examples as a bare list, so alignment is positional, and silently pairing a
sentence with the wrong translation is worse than leaving it empty.  A row
whose material no longer matches the corpus is reported and skipped.

A row may instead be a plain list of translations, one per example in the
corpus order (``{key: ["...", "..."]}``).  That shape is used where the
corpus text carries transcription damage no one wants to retype; the count
check still runs, and the reviewer is expected to read the pairings back.

A row that already carries a full ``example_en`` array is left alone, so the
script is idempotent and can be re-run after the corpus grows.

Usage::

    python -m scripts.backfill_grammar_example_glosses            # report
    python -m scripts.backfill_grammar_example_glosses --write    # apply
    python -m scripts.backfill_grammar_example_glosses --lang fr  # one corpus
"""

import argparse
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(BASE, "lute", "jlpt_data", "grammar_{lang}.json")
MATERIAL = os.path.join(
    BASE, "scripts", "grammar_materials_{lang}", "example_{field}.json"
)

LANGS = ("es", "fr", "de", "ru")
FIELDS = ("en", "ko")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_material(path):
    """
    One language's material, merged from ``example_<field>.json`` and any
    ``example_<field>_<n>.json`` next to it.

    The numbered parts exist because a corpus with several hundred rows is
    easier to author in pieces; they are plain continuations, so a key that
    appears twice is a mistake and is reported.
    """
    merged = {}
    duplicates = []
    for part in sorted(glob.glob(path.replace(".json", "*.json"))):
        for key, value in load(part).items():
            if key in merged:
                duplicates.append((key, os.path.basename(part)))
            merged[key] = value
    return merged, duplicates


def plan(rows, material, field):
    """
    Which rows get which glosses.

    Returns ``(fills, already, mismatched, uncovered)``: ``fills`` is a list
    of ``(row, aligned gloss list)``, ``mismatched`` names rows whose
    material does not line up with the corpus any more, and ``uncovered``
    counts the rows the material does not mention at all.
    """
    key = "example_" + field
    fills = []
    already = 0
    mismatched = []
    uncovered = 0
    for row in rows:
        examples = [e for e in (row.get("examples") or []) if (e or "").strip()]
        if not examples:
            continue
        have = row.get(key) or []
        if len(have) == len(examples) and all((h or "").strip() for h in have):
            already += 1
            continue
        pairs = material.get(row.get("key") or "")
        if not pairs:
            uncovered += 1
            continue
        if all(isinstance(pair, str) for pair in pairs):
            # Positional shape: the translations line up with the examples
            # by index, so only the count can be checked here.
            if len(pairs) != len(examples):
                mismatched.append(
                    (
                        row.get("key"),
                        -1,
                        f"{len(examples)} examples",
                        f"{len(pairs)} translations",
                    )
                )
                continue
            fills.append((row, [pair.strip() for pair in pairs]))
            continue
        aligned = []
        for index, example in enumerate(examples):
            source, gloss = pairs[index] if index < len(pairs) else (None, None)
            if source is None or source.strip() != example.strip():
                mismatched.append((row.get("key"), index, example, source))
                break
            aligned.append(gloss.strip())
        else:
            if len(pairs) != len(examples):
                mismatched.append(
                    (
                        row.get("key"),
                        -1,
                        f"{len(examples)} examples",
                        f"{len(pairs)} pairs",
                    )
                )
                continue
            fills.append((row, aligned))
    return fills, already, mismatched, uncovered


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply the fills")
    parser.add_argument("--lang", choices=LANGS, help="one corpus only")
    args = parser.parse_args(argv)

    langs = (args.lang,) if args.lang else LANGS
    failed = False
    for lang in langs:
        corpus_path = CORPUS.format(lang=lang)
        if not os.path.exists(corpus_path):
            print(f"{lang}: no corpus at {corpus_path}")
            failed = True
            continue
        rows = load(corpus_path)
        for field in FIELDS:
            material_path = MATERIAL.format(lang=lang, field=field)
            if not os.path.exists(material_path):
                print(
                    f"{lang}/{field}: no material ({os.path.basename(material_path)})"
                )
                continue
            material, duplicates = load_material(material_path)
            for key, part in duplicates:
                print(f"   DUPLICATE {key} in {part}")
            if duplicates:
                failed = True
            fills, already, mismatched, uncovered = plan(rows, material, field)
            print(
                f"{lang}/{field}: {len(fills)} to fill, {already} already, "
                f"{uncovered} uncovered, {len(mismatched)} mismatched"
            )
            for key, index, example, source in mismatched[:10]:
                print(
                    f"   MISMATCH {key}[{index}]: corpus={example!r} material={source!r}"
                )
            if mismatched:
                failed = True
            if args.write and fills:
                for row, aligned in fills:
                    row["example_" + field] = aligned
                # The corpora ship as indent=1 with no trailing newline;
                # matching that keeps the diff to the rows that changed.
                with open(corpus_path, "w", encoding="utf-8") as fh:
                    json.dump(rows, fh, ensure_ascii=False, indent=1)
                print(f"   wrote {len(fills)} rows to {os.path.basename(corpus_path)}")
    if failed and not args.write:
        print("report only: fix the mismatches above before writing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
