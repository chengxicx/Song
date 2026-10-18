"""Merge curated Spanish grammar materials into the built-in Spanish library.

Reads every ``units*.json`` in ``scripts/grammar_materials_es/`` (one array of
curated material entries each) and appends one row per entry to
``lute/jlpt_data/grammar_es.json``:

    key, name, slug, meaning, type, examples, example_zh, focus, zh,
    level, formation, notes

More than one file because the corpus has more than one source: ``units.json``
is the first extraction, ``units_ref.json`` a later one, ``units_prac.json`` a
third.  Files are read in that order and the first row to claim a name keeps
it, so a point two sources describe is never duplicated.

The runtime data layer (``grammar_analysis_es._load_data_rules``) derives
each row's matcher from ``focus`` and keeps it only when the derived spec
fires on the row's own examples, so this merge never has to trust a stale
spec.  A row whose focus cannot be validated on its examples still lands
in the data file, but the panel will drop it silently -- the merge warns
about it when spaCy + the Spanish model are importable.

Guards (both idempotent):
  * key     ``esm_<stem>__<md5-6>`` -- stable across re-runs
  * name    a row whose folded name collides with a hand-written rule is
            skipped: the loader would drop it anyway, and two panel rows
            on the same point are worse than one

Usage::

    python -m scripts.merge_grammar_es_materials            # report only
    python -m scripts.merge_grammar_es_materials --write    # apply
"""

import argparse
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_DIR = os.path.join(BASE, "scripts", "grammar_materials_es")
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_es.json")

# The material files, in the order the merge walks them.
_MATERIAL_FILES = ("units.json", "units_ref.json", "units_prac.json")


def make_key(name):
    stem = re.sub(r"[^0-9A-Za-zÀ-ſ]+", "", name)
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"esm_{stem or 'x'}__{h}"


def load_library():
    "The library rows, or an empty list while the file has not been created yet."
    if not os.path.exists(LIBRARY):
        print(f"note: {LIBRARY} not found -- starting from an empty library")
        return []
    with open(LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


def load_materials():
    """
    Every material entry, in file order.

    The files are walked in ``_MATERIAL_FILES`` order; ``build_rows`` keeps the
    first row that claims a name, so an earlier source wins a collision.
    """
    entries = []
    for fname in _MATERIAL_FILES:
        path = os.path.join(MATERIALS_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            entries.extend(json.load(fh))
    return entries


def _handwritten_norms():
    """
    Folded names of the hand-written Spanish rules, via the engine itself.

    Import is safe without spaCy: the engine imports its tokenizer lazily.
    """
    sys.path.insert(0, BASE)
    from lute.read.render.grammar_analysis_es import (  # noqa: E402
        _HANDWRITTEN_NORMS,
        _norm_name,
    )

    return _norm_name, _HANDWRITTEN_NORMS


def validate_row(meta, examples):
    """
    True when the focus-derived spec fires on at least one example.

    Needs spaCy + es_core_news_md; returns None (unknown) when they are
    not installed, so a validation-less machine still merges data that the
    runtime will re-check on first use.
    """
    try:
        from lute.read.render import grammar_analysis_es as es_engine  # noqa: F401
    except ImportError:
        return None
    focus = meta.get("focus") or []
    specs = es_engine._focus_specs(focus)  # pylint: disable=protected-access
    if not specs:
        return False
    try:
        return any(
            es_engine._flash(s, examples)
            for s in specs  # pylint: disable=protected-access
        )
    except ImportError:
        # _flash tokenises lazily; the Spanish model may still be missing.
        return None


def build_rows(materials, taken_keys, taken_names):
    norm_name, handwritten = _handwritten_norms()
    rows, skipped = [], []
    for meta in materials:
        name = (meta.get("name_es") or meta.get("pattern") or "").strip()
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
        examples = [e.get("spanish", "").strip() for e in raw if e.get("spanish")]
        if not examples:
            skipped.append((name, "no examples"))
            continue
        ok = validate_row(meta, examples)
        if ok is False:
            skipped.append((name, "focus spec never fires on its examples"))
            continue
        rows.append(
            {
                "key": key,
                "name": name,
                "slug": (meta.get("pattern") or "").strip(),
                "meaning": (meta.get("meaning_en") or "").strip(),
                "type": "composite",
                "examples": examples,
                "example_zh": [
                    e.get("chinese", "").strip() for e in raw if e.get("spanish")
                ],
                "focus": list(meta.get("focus") or []),
                "zh": (meta.get("meaning_zh") or "").strip(),
                "level": meta.get("level") or "B1",
                "formation": (meta.get("formation") or "").strip(),
                "notes": (meta.get("notes") or "").strip(),
            }
        )
        if ok is None:
            print(f"  ? {name}: spec unvalidated (spaCy missing) -- runtime re-checks")
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
        print(f"no materials at {MATERIALS_DIR}")
        return 1
    taken_keys = {e["key"] for e in entries}
    taken_names = {e["name"] for e in entries}
    rows, skipped = build_rows(materials, taken_keys, taken_names)
    print(f"new rows: {len(rows)}  skipped: {len(skipped)}")
    for name, why in skipped:
        print(f"  - {name} ({why})")
    if not args.write:
        print("(dry run: pass --write)")
        return 0
    entries.extend(rows)
    with open(LIBRARY, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=1)
    print(f"{LIBRARY}: now {len(entries)} rows (+{len(rows)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
