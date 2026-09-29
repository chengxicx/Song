"""Merge audited Korean grammar materials into the built-in Korean library.

Reads ``scripts/grammar_materials_ko/audit_ko_missing.json`` (written by
``scripts.audit_grammar_ko_coverage``) and appends one row per material
entry to ``lute/jlpt_data/grammar_ko.json``, keeping that file's
kimchi-grammar schema so ``grammar_analysis_ko`` loads the new rows
unchanged:

    key, name, slug, meaning, type, examples, focus, zh, level, ko,
    formation, notes

Field mapping
-------------
  name     the material pattern with its slot markers stripped
           (``V-(으)ㄹ 겸 -(으)ㄹ 겸`` -> ``-(으)ㄹ 겸 -(으)ㄹ 겸``)
  meaning  the material's English gloss
  zh / ko  the material's Chinese / Korean gloss
  level    the material's TOPIK band
  examples the example sentences, verbatim Korean (list of strings, like the
           kimchi rows); ``example_zh`` / ``example_en`` carry the parallel
           translations the panel's folded reference block quotes
  focus    left empty: the engine derives the matcher from ``name``
  type     derived from the slot marker (V/A -> verb, N -> noun, else
           composite)
  key      ``kgm_<name>__<md5-6>`` -- stable across re-runs, so the merge
           is idempotent
  formation / notes
           the book's 接续 line and 注意点, verbatim.  These two were
           dropped when the merge only kept the kimchi schema, which left
           the Korean panel showing a description while the Japanese one
           showed 接续 / 参考例句 / 注意点.  ``notes`` is English book prose
           and ``formation`` Korean, so the Chinese wording lives in
           ``grammar_ko_enrichment.json`` (the engine's display-language
           filter hides an untranslated field rather than printing it under
           a Chinese heading).

Usage::

    python -m scripts.merge_grammar_ko_materials            # report only
    python -m scripts.merge_grammar_ko_materials --write    # apply
"""

import argparse
import hashlib
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS = os.path.join(BASE, "scripts", "grammar_materials_ko")
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar_ko.json")

# Review pages that merely recombine two points which are merged separately.
_SKIP_PATTERNS = {"V-(으)ㄹ걸 그랬다, A/V-았/었어야 했는데"}

# Slot descriptors, longest first, matched only when not adjacent to a
# Latin letter (so a stray "A" inside an English word is never eaten).
_SLOT_RE = re.compile(
    r"(?<![A-Za-z])(?:A/V|V/A|N/A|V/N|ANV|NV|AV|VA|Adj|Verb|Noun|V|A|N)(?![A-Za-z])"
)
_TILDES = re.compile(r"[~〜∼]")
_SEP = re.compile(r"[\s\-–—]+")


def clean_name(pattern):
    "Strip slot markers and tidy separators; keep (으)/jamo/alternatives."
    n = _TILDES.sub("", pattern or "")
    n = _SLOT_RE.sub(" ", n)
    n = re.sub(r"\s*-\s*", "-", n)
    n = re.sub(r"\s+", " ", n).strip(" -")
    return n or (pattern or "").strip()


def derive_type(pattern):
    "Coarse type from the leading slot marker."
    p = (pattern or "").strip()
    m = re.match(r"^\s*(A/V|V/A|ANV|NV|AV|VA|Adj|Verb|Noun|V|A|N)", p)
    if not m:
        return "composite"
    slot = m.group(1)
    if "N" in slot and "V" not in slot and "A" not in slot:
        return "noun"
    return "verb"


def make_key(name):
    stem = re.sub(r"[^0-9A-Za-z가-힣]+", "", name)
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"kgm_{stem or 'x'}__{h}"


def load_missing():
    """
    The audit's gap report, or None when it has not been generated.

    An *empty* report is a legitimate result ("nothing left to merge") and
    must not be confused with a missing one -- conflating them makes a clean
    run look like a forgotten step.
    """
    path = os.path.join(MATERIALS, "audit_ko_missing.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# The three book files, in the order the audit walks them.
_MATERIAL_FILES = ("beginning.json", "intermediate.json", "advanced.json")

# The panel fields the merge carries from the book, as (row field, material
# field).  ``backfill`` uses the same list so a re-run repairs both.
_PANEL_FIELDS = (("formation", "formation"), ("notes", "notes"))


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
                name = clean_name(meta.get("pattern") or "")
                if name and name not in by_name:
                    by_name[name] = meta
    return by_name


def backfill(entries, materials_by_name):
    """
    Fill the panel fields on rows merged before the merge carried them.

    Idempotent and non-destructive: only a *missing* field is filled, so a
    hand-corrected value survives a re-run -- the same rule the zh/ko glosses
    follow in ``enrich_grammar_ko``.  Returns the (key, field) pairs changed,
    which is empty on a second run.
    """
    filled = []
    for e in entries:
        if not e["key"].startswith("kgm_"):
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
        name = clean_name(pattern)
        if not name:
            skipped.append((pattern, "no name"))
            continue
        key = make_key(name)
        if key in taken_keys or name in taken_names:
            skipped.append((pattern, "already present"))
            continue
        examples = [
            e.get("korean", "").strip()
            for e in meta.get("examples", [])
            if e.get("korean")
        ]
        rows.append(
            {
                "key": key,
                "name": name,
                "slug": "",
                "meaning": (meta.get("meaning_en") or "").strip(),
                "type": derive_type(pattern),
                "examples": examples,
                "example_zh": [
                    e.get("chinese", "").strip()
                    for e in meta.get("examples", [])
                    if e.get("korean")
                ],
                "example_en": [
                    e.get("english", "").strip()
                    for e in meta.get("examples", [])
                    if e.get("korean")
                ],
                "focus": [],
                "zh": (meta.get("meaning_zh") or "").strip(),
                "level": meta.get("level") or "TOPIK 3-4",
                "ko": (meta.get("meaning_ko") or "").strip(),
                "source": meta.get("source") or "private-book",
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
    with open(LIBRARY, encoding="utf-8") as fh:
        entries = json.load(fh)
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
        print("no audit_ko_missing.json yet -- run scripts.audit_grammar_ko_coverage")
        if args.write and filled:
            with open(LIBRARY, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=False, indent=1)
            print(f"{LIBRARY}: back-fill written")
        return 1
    if not missing:
        print("audit_ko_missing.json is empty -- nothing to merge")
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
