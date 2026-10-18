"""Merge German grammar materials into the built-in German library.

Reads the curated material files under ``scripts/grammar_materials_de/``
(``units.json`` -- the first extraction -- then ``units_ref.json``, a later
one) and appends one row per entry to ``lute/jlpt_data/grammar_de.json``:

    key, name, slug, meaning, type, examples, example_zh, focus, zh,
    level, formation, notes

Field mapping
-------------
  name     the material's grammar ``pattern`` (the point: "Wegen / Trotz +
           Genitiv"), falling back to the raw ``name_de`` headword when it
           has none -- see ``row_name``
  meaning  the material's English gloss
  zh       the material's Chinese gloss
  level    the material's CEFR band (e.g. ``A1``), ``B1`` when absent
  examples the example sentences, verbatim German (list of strings);
           ``example_zh`` carries the parallel Chinese translations
  focus    the material's ``focus`` literals, verbatim.  The German data
           layer derives its matcher from this field, so it must be
           preserved rather than emptied -- an empty ``focus`` silently
           disables matching for the row
  type     kept deliberately coarse: always ``composite``
  key      ``dgm_<name>__<md5-6>`` -- stable across re-runs, so the merge
           is idempotent
    formation / notes
           the material's formation line and notes, verbatim
    slug   the material's original ``pattern``, verbatim

The runtime data layer (``grammar_analysis_de._load_data_rules``) derives
each row's matcher from ``focus`` and keeps it only when the derived spec
fires on the row's own examples, so this merge never has to trust a stale
spec.  A row whose focus cannot be validated on its examples still lands in
the data file, but the panel will drop it silently -- the merge warns about
it when spaCy + the German model are importable.

An older merge stored a row under the material's ``name_de`` headword while
recording its ``pattern`` in ``slug``; ``normalise_names`` repairs those
rows to the rule the rest of the library follows: the pattern is the name.

Guards (both idempotent):
  * key     ``dgm_<stem>__<md5-6>`` -- stable across re-runs
  * name    a row whose folded name collides with a hand-written rule is
            skipped: the loader would drop it anyway, and two panel rows
            on the same point are worse than one

Usage::

    python -m scripts.merge_grammar_de_materials                 # report only
    python -m scripts.merge_grammar_de_materials --write         # apply
    python -m scripts.merge_grammar_de_materials --file X.json   # one fragment
"""

import argparse
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_DIR = os.path.join(BASE, "scripts", "grammar_materials_de")
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_de.json")

# The material files, in the order the merge walks them.  More than one
# because the corpus is built from more than one source; ``units.json`` is
# the first extraction, ``units_ref.json`` a later one.  First wins on a
# name collision, so a point both files describe is never duplicated.
_MATERIAL_FILES = ("units.json", "units_ref.json")


def make_key(name):
    stem = re.sub(r"[^0-9A-Za-zÀ-ſ]+", "", name)
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"dgm_{stem or 'x'}__{h}"


def row_name(meta):
    """
    The name a material row is merged under: its grammar pattern, else its
    German headword.

    ``pattern`` names the *point* ("Wegen / Trotz + Genitiv"); ``name_de`` is
    the extraction's headword ("wegen / trotz + Genitiv").  Storing the row
    under the pattern makes the name the same string the rest of the library
    uses, which is what the Russian library settled on too.
    """
    return (meta.get("pattern") or "").strip() or (meta.get("name_de") or "").strip()


def load_library():
    "The library rows, or an empty list while the file has not been created yet."
    if not os.path.exists(LIBRARY):
        print(f"note: {LIBRARY} not found -- starting from an empty library")
        return []
    with open(LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


def load_materials(files=None):
    "Every material entry, in file order."
    entries = []
    for fname in files or _MATERIAL_FILES:
        path = fname if os.path.isabs(fname) else os.path.join(MATERIALS_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            entries.extend(json.load(fh))
    return entries


def _handwritten_norms():
    """
    Folded names of the hand-written German rules, via the engine itself.

    Import is safe without spaCy: the engine imports its tokenizer lazily.
    """
    sys.path.insert(0, BASE)
    from lute.read.render.grammar_analysis_de import (  # noqa: E402
        _HANDWRITTEN_NORMS,
        _norm_name,
    )

    return _norm_name, _HANDWRITTEN_NORMS


def validate_row(meta, examples):
    """
    True when the focus-derived spec fires on at least one example.

    Needs spaCy + de_core_news_sm; returns None (unknown) when they are
    not installed, so a validation-less machine still merges data that the
    runtime will re-check on first use.
    """
    try:
        from lute.read.render import grammar_analysis_de as de_engine  # noqa: F401
    except ImportError:
        return None
    focus = meta.get("focus") or []
    specs = de_engine._focus_specs(focus)  # pylint: disable=protected-access
    if not specs:
        return False
    try:
        return any(
            de_engine._flash(s, examples) for s in specs
        )  # pylint: disable=protected-access
    except ImportError:
        # _flash tokenises lazily; the German model may still be missing.
        return None


def normalise_names(entries):
    """
    Name every row after its pattern, the way ``build_rows`` does.

    A row merged by the older script carries its ``name_de`` headword in
    ``name`` and the material's pattern in ``slug``; the two disagree on
    exactly those rows (they differ only in case).  Since the name is the
    panel heading and the key is derived from it, the repair is to move the
    pattern into ``name`` and re-key the row.

    Idempotent: a row already named after its pattern is left alone, so this
    reports nothing on a second run.  Returns the (old key, new key) pairs
    changed.
    """
    renamed = []
    for e in entries:
        if not e["key"].startswith("dgm_"):
            continue
        pattern = (e.get("slug") or "").strip()
        if not pattern or pattern == (e.get("name") or "").strip():
            continue
        old = e["key"]
        e["name"] = pattern
        e["key"] = make_key(pattern)
        renamed.append((old, e["key"]))
    return renamed


def build_rows(materials, taken_keys, taken_names):
    norm_name, handwritten = _handwritten_norms()
    rows, skipped = [], []
    for meta in materials:
        name = row_name(meta)
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
        examples = [e.get("german", "").strip() for e in raw if e.get("german")]
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
                    e.get("chinese", "").strip() for e in raw if e.get("german")
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
    ap.add_argument(
        "--file",
        action="append",
        default=None,
        help="material file(s) to read instead of the default pair",
    )
    args = ap.parse_args()
    entries = load_library()
    materials = load_materials(args.file)
    if not materials:
        print(f"no materials under {MATERIALS_DIR}")
        return 1

    # Repair first: a fresh merge needs the material files, and the repair
    # must land either way, so it runs before the new rows are built.
    renamed = normalise_names(entries)
    print(f"rows renamed to their pattern: {len(renamed)}")
    for old, new in renamed[:10]:
        print(f"  {old} -> {new}")
    if len(renamed) > 10:
        print(f"  ... and {len(renamed) - 10} more")

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
