"""Merge Arabic grammar materials into the built-in Arabic library.

Reads ``scripts/grammar_materials_ar/units.json`` (one array of curated
material entries) and appends one row per entry to
``lute/jlpt_data/grammar_ar.json``:

    key, name, slug, meaning, type, examples, example_zh, focus, zh,
    level, formation, notes, at_start

The runtime data layer (``grammar_analysis_ar._load_data_rules``) derives
each row's matcher from ``focus`` and keeps it only when the derived spec
fires on the row's own examples, so this merge never has to trust a stale
spec.  A row whose focus cannot be validated on its examples still lands
in the data file, but the panel will drop it silently -- the merge warns
about it.

Guards (both idempotent):

  * key     ``agm_<stem>__<md5-6>`` -- stable across re-runs
  * name    a row whose folded name collides with a hand-written rule is
            skipped: the loader would drop it anyway, and two panel rows
            on the same point are worse than one

``--write`` refreshes as well as appends: the library is a pure function of
the materials (same keys, same order), so a row whose ``focus`` or wording
changed in the source file is rewritten in place instead of being left at
its old content.  A row that no longer has a material entry is reported as
stale and left alone -- dropping a published grammar point is a decision,
not a side effect of a merge.

Usage::

    python -m scripts.merge_grammar_ar_materials            # report only
    python -m scripts.merge_grammar_ar_materials --write    # apply
"""

import argparse
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_PATH = os.path.join(BASE, "scripts", "grammar_materials_ar", "units.json")
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_ar.json")


def make_key(name):
    stem = re.sub(r"[^0-9A-Za-z\u0600-\u06FF]+", "", name)
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"agm_{stem or 'x'}__{h}"


def load_library():
    "The library rows, or an empty list while the file has not been created yet."
    if not os.path.exists(LIBRARY):
        print(f"note: {LIBRARY} not found -- starting from an empty library")
        return []
    with open(LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


def load_materials():
    "Every material entry, in file order."
    if not os.path.exists(MATERIALS_PATH):
        return []
    with open(MATERIALS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _handwritten_norms():
    """
    Folded names of the hand-written Arabic rules, via the engine itself.

    Import is safe without pyarabic: the engine imports it lazily.
    """
    sys.path.insert(0, BASE)
    from lute.read.render.grammar_analysis_ar import (  # noqa: E402
        _HANDWRITTEN_NORMS,
        _norm_name,
    )

    return _norm_name, _HANDWRITTEN_NORMS


def validate_row(meta, examples):
    """
    True when the focus-derived spec fires on at least one example.

    The Arabic engine needs no external model, so unlike the French merge
    this always returns a verdict -- there is no "unknown" case.
    """
    from lute.read.render import grammar_analysis_ar as ar_engine

    specs = ar_engine._focus_specs(
        meta.get("focus") or []
    )  # pylint: disable=protected-access
    if not specs:
        return False
    return any(
        ar_engine._flash(s, examples) for s in specs  # pylint: disable=protected-access
    )


def build_rows(materials, taken_keys, taken_names):
    norm_name, handwritten = _handwritten_norms()
    rows, skipped = [], []
    for meta in materials:
        name = (meta.get("name_ar") or meta.get("pattern") or "").strip()
        if not name:
            skipped.append((meta.get("pattern") or "?", "no name"))
            continue
        if norm_name(name) in handwritten:
            skipped.append((name, "hand-written rule already covers it"))
            continue
        key = make_key(name)
        if key in taken_keys or name in taken_names:
            skipped.append((name, "already present"))
            continue
        raw = meta.get("examples") or []
        examples = [e.get("arabic", "").strip() for e in raw if e.get("arabic")]
        if not examples:
            skipped.append((name, "no examples"))
            continue
        if not validate_row(meta, examples):
            skipped.append((name, "focus spec never fires on its examples"))
            continue
        row = {
            "key": key,
            "name": name,
            "slug": (meta.get("pattern") or "").strip(),
            "meaning": (meta.get("meaning_en") or "").strip(),
            "type": "composite",
            "examples": examples,
            "example_zh": [
                e.get("chinese", "").strip() for e in raw if e.get("arabic")
            ],
            "focus": list(meta.get("focus") or []),
            "zh": (meta.get("meaning_zh") or "").strip(),
            "level": meta.get("level") or "B1",
            "formation": (meta.get("formation") or "").strip(),
            "notes": (meta.get("notes") or "").strip(),
        }
        if meta.get("at_start"):
            row["at_start"] = True
        rows.append(row)
        taken_keys.add(key)
        taken_names.add(name)
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    entries = load_library()
    materials = load_materials()
    if not materials:
        print(f"no materials at {MATERIALS_PATH}")
        return 1
    # Both dedupe sets start empty: the rebuild is the full library, and the
    # merge step below decides what is new against the file on disk.
    rows, skipped = build_rows(materials, set(), set())
    position = {entry["key"]: i for i, entry in enumerate(entries)}
    new = [r for r in rows if r["key"] not in position]
    updated = [
        r for r in rows if r["key"] in position and entries[position[r["key"]]] != r
    ]
    stale = [k for k in position if k not in {r["key"] for r in rows}]
    print(
        f"materials: {len(rows)} rows  new: {len(new)}  "
        f"updated: {len(updated)}  stale: {len(stale)}  skipped: {len(skipped)}"
    )
    for name, why in skipped:
        print(f"  - {name} ({why})")
    for key in stale:
        print(f"  ! {key}: no material entry derives it (left in place)")
    for row in updated:
        print(f"  ~ {row['key']}")
    if not args.write:
        print("(dry run: pass --write)")
        return 0
    for row in rows:
        if row["key"] in position:
            entries[position[row["key"]]] = row
        else:
            position[row["key"]] = len(entries)
            entries.append(row)
    with open(LIBRARY, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=1)
    print(
        f"{LIBRARY}: now {len(entries)} rows (+{len(new)} new, ~{len(updated)} updated)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
