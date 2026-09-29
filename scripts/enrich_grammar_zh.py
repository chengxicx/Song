"""Prefer the Chinese study materials' own wording for the vendored rows.

The 595 jkindrix rows carry English 接续 / 注意点 / 例句译文; the Chinese
for them lives in ``lute/jlpt_data/grammar/zh_enrichment.json`` (see the
panel bug it fixes in that directory's ``ATTRIBUTION.md``).  Where a
*Chinese* study material covers the same grammar point, the textbook's
own Chinese beats a translation of the English prose -- but only one of
the two enrichment fields survives that test:

* **接续** -- taken from the material.  A textbook's 接续 is the
  authoritative Chinese notation for the pattern.  A field that turns out
  to hold a *meaning* list instead (``1、表示推测；2、…``, ``表示具有…``)
  is rejected: the materials put both kinds of text in that column, and a
  meaning there would show up on the panel's 接续 line.
* **注意点** -- kept as the translation, except when the material's note
  actually names this row's own pattern.  The materials' notes are mostly
  terse cross-references (``类似用法还有「ずくめ」``), and the vendored
  notes are real usage warnings (``Do NOT use あげる for gifts TO you``)
  whose translation carries more.  But a note written for a neighbouring
  point is worse than either -- ``だけでなく``'s material note is about
  ``ひとり``, ``でも``'s about ``〜も`` -- so a note is taken only when it
  mentions the row's own pattern.
* **例句译文** -- taken per sentence, only when the material carries that
  exact Japanese sentence.  The two corpora barely overlap (1 of 1787),
  so this is a correction rather than a source, and a sentence can never
  be mis-assigned.

Example translations stay keyed by the Japanese sentence.  Re-running is
idempotent: a field with no usable material source keeps its translation.

A material entry may supply a row only when both patterns share the same
longest Japanese fragment, so から never picks up からある and て form
never picks up 〜て（は）たまらない.  Among the survivors the most useful
entry wins (shared example sentence, then a usable note, then the most
detailed 接续).

Usage::

    python -m scripts.enrich_grammar_zh            # report only
    python -m scripts.enrich_grammar_zh --write    # apply
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:  # `python scripts/enrich_grammar_zh.py` also works
    sys.path.insert(0, BASE)

from scripts.audit_grammar_coverage import _slug  # noqa: E402

MATERIALS = os.path.join(BASE, "scripts", "grammar_materials")
LIBRARY = os.path.join(BASE, "lute", "jlpt_data", "grammar")
ENRICHMENT = os.path.join(LIBRARY, "zh_enrichment.json")
SOURCES = os.path.join(MATERIALS, "zh_enrichment_sources.json")

# A materials `formation` column holds either the 接续 or something that
# is not a 接续 at all.  Rejected, each for a real row:
#   表示/意思/意义 -- a meaning list, how "1、表示推测；2、…" would land on
#                    the panel's 接续 line;
#   同上           -- the materials' "see the previous entry" placeholder,
#                    which means nothing once the entry is on its own row;
#   吗/呢/？/！     -- a translated sentence ("～吗？" for ませんか);
#   ?              -- an OCR-lost "/" ("用言?动词连体形" -> 用言/动词…).
_MEANING = re.compile(r"表示|意思|意义|指的是|同上|吗|呢")
_OCR_SLASH = re.compile(r"\?")
# Up to this many characters a `formation` is a bare slot label, not a
# description that could be about a different point.
_TERSE = 8


def _formation_of(row, entry):
    "The material's 接续 for this row, or '' when the column is not one."
    formation = _OCR_SLASH.sub("/", (entry.get("formation") or "").strip())
    if not formation or _MEANING.search(formation):
        return ""
    if len(formation) <= _TERSE:
        # A bare slot label (体言 / 连体形 / 动词简体) is the whole 接续 for
        # a whole family of points and cannot name a wrong one.
        return formation
    # A detailed 接续 must name the pattern it is the 接続 *of*; otherwise it
    # describes a neighbouring point -- 〜ほど's material entry carries
    # 「最小数量词+として〜ない」, which is 〜として's, not 〜ほど's.
    core = _core(_slug(row.get("pattern", "")))
    return formation if core and core in formation else ""


def _core(frags):
    """
    The pattern's longest Japanese fragment -- what the point *is*.

    Ties are broken lexicographically (the audit's own ``_core`` takes
    whichever the set happens to yield first), so a re-run cannot flip a
    row's source from one material to another.
    """
    return max(sorted(frags), key=len) if frags else ""


def load_materials():
    "Every material entry, keyed by (file, pattern) -> [entry]."
    out = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(MATERIALS, "*.json"))):
        name = os.path.basename(path)
        if name.startswith(("audit_", "zh_")):
            continue  # audit reports and generated tables, not source books
        with open(path, encoding="utf-8") as fh:
            for entry in json.load(fh):
                out[(name, entry.get("pattern", ""))].append(entry)
    return out


def load_library():
    "The library rows, keyed by entry id."
    out = {}
    for path in sorted(glob.glob(os.path.join(LIBRARY, "n[1-5].json"))):
        with open(path, encoding="utf-8") as fh:
            for entry in json.load(fh):
                out[entry["id"]] = entry
    return out


def candidates(row, materials):
    """
    Material entries that name the same grammar point as ``row``.

    The longest Japanese fragment of both patterns must be identical: it
    is the one piece of the pattern that says which point is meant, and
    requiring it to match on both sides is what keeps から away from
    からある and だけ away from だけでなく.
    """
    core = _core(_slug(row.get("pattern", "")))
    if len(core) < 2:
        return []  # a bare particle cannot identify anything
    out = []
    for (name, pattern), entries in materials.items():
        if _core(_slug(pattern)) != core:
            continue
        for entry in entries:
            if (entry.get("formation") or "").strip():
                out.append((name, entry))
    return out


def _notes_of(row, entry):
    "The material's 注意点 for this row, or '' when it is about another point."
    notes = (entry.get("notes") or "").strip()
    core = _core(_slug(row.get("pattern", "")))
    return notes if notes and core and core in notes else ""


def _rank(row, name, entry):
    "Most useful material entry first (see the module docstring)."
    want = {(x.get("japanese") or "").strip() for x in row.get("examples") or []}
    have = {(x.get("japanese") or "").strip() for x in entry.get("examples") or []}
    return (
        0 if want & have else 1,                        # shares example sentences
        0 if _notes_of(row, entry) else 1,              # carries a usable 注意点
        -len(_formation_of(row, entry)),                # more detailed 接续
        name,
        entry.get("pattern", ""),
    )


def pick(row, materials):
    "The best material entry for a row, or None."
    found = candidates(row, materials)
    if not found:
        return None
    return sorted(found, key=lambda c: _rank(row, c[0], c[1]))[0]


def build(base, library, materials):
    """
    Return ``(enrichment, sources)`` -- the upgraded wording and, for each
    row and field, whether it came from a material or stayed a translation.
    """
    enrichment, sources = {}, {}
    for entry_id, current in base.items():
        row = library.get(entry_id)
        upgraded = dict(current)
        source = {"formation": "translated", "notes": "translated",
                  "examples": "translated"}
        chosen = pick(row, materials) if row else None
        if chosen:
            name, material = chosen
            formation = _formation_of(row, material)
            if formation:
                upgraded["formation"] = formation
                source["formation"] = name
            notes = _notes_of(row, material)
            if notes:
                upgraded["notes"] = notes
                source["notes"] = name
            by_jp = {(x.get("japanese") or "").strip(): (x.get("chinese") or "").strip()
                     for x in material.get("examples") or []}
            examples = dict(upgraded.get("examples") or {})
            taken = 0
            for jp, zh in examples.items():
                if by_jp.get(jp):
                    examples[jp] = by_jp[jp]
                    taken += 1
            if taken:
                upgraded["examples"] = examples
                source["examples"] = name
        enrichment[entry_id] = upgraded
        sources[entry_id] = source
    return enrichment, sources


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="write zh_enrichment.json (default: report only)")
    args = ap.parse_args()

    with open(ENRICHMENT, encoding="utf-8") as fh:
        base = json.load(fh)
    library, materials = load_library(), load_materials()
    enrichment, sources = build(base, library, materials)

    counts = collections.Counter()
    for source in sources.values():
        for field, origin in source.items():
            counts[(field, origin != "translated")] += 1
    print(f"rows: {len(enrichment)}")
    for field in ("formation", "notes", "examples"):
        print(f"  {field}: material {counts[(field, True)]}, "
              f"translated {counts[(field, False)]}")

    if not args.write:
        print("(dry run: pass --write)")
        return 0
    for path, data in ((ENRICHMENT, enrichment), (SOURCES, sources)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())