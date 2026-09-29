"""Merge audited Korean grammar materials into the built-in Korean library.

Reads ``scripts/grammar_materials_ko/audit_ko_missing.json`` (written by
``scripts.audit_grammar_ko_coverage``) and appends one row per material
entry to ``lute/jlpt_data/grammar_ko.json``, keeping that file's
kimchi-grammar schema so ``grammar_analysis_ko`` loads the new rows
unchanged:

    key, name, slug, meaning, type, examples, focus, zh, level, ko

Field mapping
-------------
  name     the material pattern with its slot markers stripped
           (``V-(으)ㄹ 겸 -(으)ㄹ 겸`` -> ``-(으)ㄹ 겸 -(으)ㄹ 겸``)
  meaning  the material's English gloss
  zh / ko  the material's Chinese / Korean gloss
  level    the material's TOPIK band
  examples the example sentences, verbatim Korean (list of strings, like the
           kimchi rows); ``example_zh`` / ``example_en`` carry the parallel
           translations for a future browse page
  focus    left empty: the engine derives the matcher from ``name``
  type     derived from the slot marker (V/A -> verb, N -> noun, else
           composite)
  key      ``kgm_<name>__<md5-6>`` -- stable across re-runs, so the merge
           is idempotent

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
                "notes": "",
            }
        )
        taken_keys.add(key)
        taken_names.add(name)
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    missing = load_missing()
    if missing is None:
        print("no audit_ko_missing.json yet -- run scripts.audit_grammar_ko_coverage")
        return 1
    if not missing:
        print("audit_ko_missing.json is empty -- nothing to merge")
        return 0
    with open(LIBRARY, encoding="utf-8") as fh:
        entries = json.load(fh)
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
