"""Merge audited Russian grammar materials into the built-in Russian library.

Reads ``scripts/grammar_materials_ru/audit_ru_missing.json`` (written by
``scripts.audit_grammar_ru_coverage``) and appends one row per material
entry to ``lute/jlpt_data/grammar_ru.json``:

    key, name, slug, meaning, type, examples, example_zh, focus, zh,
    level, formation, notes

Field mapping
-------------
  name     the material's Russian headword (``name_ru``), falling back to
           the raw ``pattern`` when no headword is given
  meaning  the material's English gloss
  zh       the material's Chinese gloss
  level    the material's CEFR band (e.g. ``A1``), ``B1`` when absent
  examples the example sentences, verbatim Russian (list of strings);
           ``example_zh`` carries the parallel Chinese translations
  focus    the material's ``focus`` literals, verbatim.  The Russian data
           layer derives its matcher from this field, so it must be
           preserved rather than emptied -- an empty ``focus`` silently
           disables matching for the row
  type     kept deliberately coarse: ``composite`` unless the pattern
           carries an explicit infinitive marker (``-ть`` / ``+ 不定式``),
           which makes the row ``verb``
  key      ``rgm_<name>__<md5-6>`` -- stable across re-runs, so the merge
           is idempotent
    formation / notes
           the material's formation line and notes, verbatim
    slug   the material's original ``pattern``, verbatim -- the coverage
           audit's raw-pattern lookup key, so a material entry whose
           pattern is a Chinese description still maps to its row

Usage::

    python -m scripts.merge_grammar_ru_materials            # report only
    python -m scripts.merge_grammar_ru_materials --write    # apply
"""

import argparse
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS = os.path.join(BASE, "scripts", "grammar_materials_ru")
MATERIALS_PATH = os.path.join(MATERIALS, "units.json")
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_ru.json")

# Patterns that merely recombine two points which are merged separately.
_SKIP_PATTERNS = set()

# The material file, in the order the audit walks it.
_MATERIAL_FILES = ("units.json",)

# The panel fields the merge carries from the material, as (row field,
# material field).  ``backfill`` uses the same list so a re-run repairs both.
# ``slug`` carries the material's original ``pattern``: rows are stored under
# their Russian headword, and without the pattern the coverage audit cannot
# match a material entry whose pattern is a Chinese description back to the
# row it produced -- it would re-report it missing forever.
_PANEL_FIELDS = (
    ("formation", "formation"),
    ("notes", "notes"),
    ("slug", "pattern"),
)

# A pattern that carries one of these markers governs a verb; everything
# else stays the conservative default.
_INFINITIVE_MARKERS = ("-ть", "+ 不定式")


def row_name(meta):
    "The name a material row is merged under: its Russian headword or pattern."
    return (meta.get("name_ru") or "").strip() or (meta.get("pattern") or "").strip()


def derive_type(pattern):
    "Coarse type: 'verb' only on an explicit infinitive marker, else 'composite'."
    p = pattern or ""
    if any(m in p for m in _INFINITIVE_MARKERS):
        return "verb"
    return "composite"


def make_key(name):
    stem = re.sub(r"[^0-9A-Za-z\u0400-\u04FF]+", "", name)
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"rgm_{stem or 'x'}__{h}"


def load_library():
    "The library rows, or an empty list while the file has not been created yet."
    if not os.path.exists(LIBRARY):
        print(f"note: {LIBRARY} not found -- starting from an empty library")
        return []
    with open(LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


def load_missing():
    """
    The audit's gap report, or None when it has not been generated.

    An *empty* report is a legitimate result ("nothing left to merge") and
    must not be confused with a missing one -- conflating them makes a clean
    run look like a forgotten step.
    """
    path = os.path.join(MATERIALS, "audit_ru_missing.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_engine_covered():
    """
    Patterns a hand-written rule already fires on (the audit's baseline
    report).  A missing report can go stale -- written before a hand-written
    rule landed -- so the merge re-checks against it and skips those
    patterns: merging one would put two panel rows on the same point.
    """
    path = os.path.join(MATERIALS, "audit_ru_engine_covered.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_materials():
    """
    Every material row, keyed by the name the merge gives it.

    First wins on a name collision, walking the patterns in sorted order --
    the same order and the same rule ``build_rows`` uses (``sorted(missing)``
    plus ``taken_names``), so a back-fill can only ever reach the row the
    merge itself would have created.
    """
    by_name = {}
    for fname in _MATERIAL_FILES:
        path = os.path.join(MATERIALS, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for meta in sorted(json.load(fh), key=lambda m: m.get("pattern") or ""):
                name = row_name(meta)
                if name and name not in by_name:
                    by_name[name] = meta
    return by_name


def backfill(entries, materials_by_name):
    """
    Fill the panel fields on rows merged before the merge carried them.

    Idempotent and non-destructive: only a *missing* field is filled, so a
    hand-corrected value survives a re-run.  Returns the (key, field) pairs
    changed, which is empty on a second run.
    """
    filled = []
    for e in entries:
        if not e["key"].startswith("rgm_"):
            continue
        meta = materials_by_name.get(e.get("name") or "")
        if not meta:
            continue
        for field, src in _PANEL_FIELDS:
            want = (meta.get(src) or "").strip()
            if want and not (e.get(field) or "").strip():
                e[field] = want
                filled.append((e["key"], field))
    return filled


def existing(entries):
    return {e["key"] for e in entries}, {e["name"] for e in entries}


def build_rows(missing, taken_keys, taken_names):
    rows, skipped = [], []
    for pattern, meta in sorted(missing.items()):
        if pattern in _SKIP_PATTERNS:
            skipped.append((pattern, "review page"))
            continue
        name = row_name(meta) or (pattern or "").strip()
        if not name:
            skipped.append((pattern, "no name"))
            continue
        key = make_key(name)
        if key in taken_keys or name in taken_names:
            skipped.append((pattern, "already present"))
            continue
        raw = meta.get("examples") or []
        rows.append(
            {
                "key": key,
                "name": name,
                "slug": (pattern or "").strip(),
                "meaning": (meta.get("meaning_en") or "").strip(),
                "type": derive_type(pattern),
                "examples": [
                    e.get("russian", "").strip() for e in raw if e.get("russian")
                ],
                "example_zh": [
                    e.get("chinese", "").strip() for e in raw if e.get("russian")
                ],
                "focus": list(meta.get("focus") or []),
                "zh": (meta.get("meaning_zh") or "").strip(),
                "level": meta.get("level") or "B1",
                "formation": (meta.get("formation") or "").strip(),
                "notes": (meta.get("notes") or "").strip(),
            }
        )
        taken_keys.add(key)
        taken_names.add(name)
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    entries = load_library()
    materials_by_name = load_materials()

    # Repair first, so the run reports the panel fields either way: a
    # back-fill only touches rows that already exist, while a fresh merge
    # needs the audit report.
    filled = backfill(entries, materials_by_name)
    print(f"panel fields back-filled: {len(filled)}")
    for key, field in filled[:10]:
        print(f"  + {key} .{field}")
    if len(filled) > 10:
        print(f"  ... and {len(filled) - 10} more")

    missing = load_missing()
    if missing is None:
        print("no audit_ru_missing.json yet -- run scripts.audit_grammar_ru_coverage")
        if args.write and filled:
            with open(LIBRARY, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=False, indent=1)
            print(f"{LIBRARY}: back-fill written")
        return 1
    if not missing:
        print("audit_ru_missing.json is empty -- nothing to merge")
        if args.write:
            if filled:
                with open(LIBRARY, "w", encoding="utf-8") as fh:
                    json.dump(entries, fh, ensure_ascii=False, indent=1)
                print(f"{LIBRARY}: back-fill written")
            else:
                print("(nothing to write: no new rows, no back-fill)")
        else:
            print("(dry run: pass --write)")
        return 0

    tk, tn = existing(entries)
    engine_covered = load_engine_covered()
    stale = [p for p in missing if p in engine_covered]
    for p in stale:
        del missing[p]
    if stale:
        print(f"skipped {len(stale)} stale pattern(s): now hand-written-covered")
        for p in stale[:5]:
            print(f"  - {p}")
    rows, skipped = build_rows(missing, tk, tn)
    print(f"new rows: {len(rows)}  skipped: {len(skipped)}")
    for p, why in skipped:
        print(f"  - {p} ({why})")
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
