"""Audit the Russian grammar material file against the built-in Russian library.

Reads ``scripts/grammar_materials_ru/units.json`` (one array of material
entries) and matches each entry against the vendored library
``lute/jlpt_data/grammar_ru.json``.

Matching is by normalized Russian core: the alternative marker
``~ / 〜 / ∼ / - / – / —`` and every space, punctuation mark and slot
descriptor are stripped, the text is lower-cased, and only Latin letters,
digits and Cyrillic are kept; the remaining forms are compared.  A material
pattern may list alternatives separated by ``,`` or ``/`` (e.g.
``быть / являться``); each alternative is matched independently.

Three reports are written next to the materials:

  audit_ru_missing.json        material entries with no library counterpart
                               (candidates for new library rows)
  audit_ru_supplementable.json library entries matched by a material entry
                               but lacking a Chinese gloss, an English
                               meaning, a level or examples
  audit_ru_confusables.json    near-miss pairs (fragment-overlap only) for
                               human review -- the audit never merges across
                               a confusable pair on its own

Two rules are inherited verbatim from the Korean audit; both were learned
the hard way and must not be relaxed:

1. Containment is **one-directional** (``material in library`` only).  The
   reverse test looks harmless -- the library name sits inside the material
   pattern -- but it silently "covers" unrelated longer points with any
   short row that happens to be a substring, and it once hid 39 real gaps
   behind a report that claimed "0 missing".
2. A same-point-different-name case is a *judgement*, not a substring test.
   Those pairs belong in ``_EXPLICIT_MATCH``, where each one is reviewable,
   and never in an automatic containment match.

Usage::

    python -m scripts.audit_grammar_ru_coverage            # write the reports
    python -m scripts.audit_grammar_ru_coverage --print    # summary
    python -m scripts.audit_grammar_ru_coverage --check    # gate: every
                                                           # entry mapped once
"""

import argparse
import collections
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_DIR = os.path.join(BASE, "scripts", "grammar_materials_ru")
MATERIALS_PATH = os.path.join(MATERIALS_DIR, "units.json")
LIBRARY_PATH = os.path.join(BASE, "lute", "jlpt_data", "grammar_ru.json")

# Slot descriptors a pattern may use to name the word class a point
# attaches to.  Longest first so "A/V" is stripped before bare "A".  The
# Latin tokens are word-class markers, not Russian text; the Cyrillic ones
# are the conventional abbreviations a Russian grammar outline uses.
_SLOT_WORDS = [
    "A/V",
    "V/A",
    "N/A",
    "V/N",
    "AV",
    "VA",
    "Adj/Verb",
    "Adj",
    "Verb",
    "Noun",
    "инфинитив",
    "глагол",
    "нареч",
    "прил",
    "сущ",
    "V",
    "A",
    "N",
]

# Material patterns that name the same grammar point as an existing library
# entry under a different surface form.  Each pair is a human judgement and
# must be hand-reviewed before it is added here; the audit maps them so no
# near-duplicate row is created.  Empty until such a pair has been reviewed
# -- an empty table is a deliberate state, not a missing step.
_EXPLICIT_MATCH = {}

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_TILDE = re.compile(r"[~〜∼\-–—]")
_DROP = re.compile(r"[^0-9A-Za-z\u0400-\u04FF]+")
_SLOT_RE = re.compile(
    r"(?<![A-Za-z\u0400-\u04FF])(?:"
    + "|".join(sorted(_SLOT_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z\u0400-\u04FF])"
)


def _canon(text):
    "Normalize a pattern to its bare core (Latin/Cyrillic letters + digits)."
    t = _TILDE.sub(" ", text or "")
    t = _SLOT_RE.sub(" ", t)
    return _DROP.sub("", t).lower()


def _alts(pattern):
    "Alternative cores a material pattern may stand for."
    out = []
    for part in re.split(r"[,、]", pattern or ""):
        part = part.strip()
        if not part:
            continue
        whole = _canon(part)
        if whole:
            out.append(whole)
        for piece in re.split(r"/", part):
            c = _canon(piece)
            if c and c not in out:
                out.append(c)
    seen, uniq = set(), []
    for a in out:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def _core(text):
    "Longest contiguous Cyrillic run in a pattern (after slot stripping)."
    t = _SLOT_RE.sub(" ", _TILDE.sub(" ", text or ""))
    best, cur = "", ""
    for ch in t:
        if _CYRILLIC.match(ch):
            cur += ch
            if len(cur) > len(best):
                best = cur
        else:
            cur = ""
    return best


def _load_library():
    "The vendored library keyed by ``key``; empty while it is not generated yet."
    if not os.path.exists(LIBRARY_PATH):
        print(f"note: {LIBRARY_PATH} not found -- nothing to match against")
        return {}
    with open(LIBRARY_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)
    return {e["key"]: e for e in entries}


def _load_materials():
    "Every material entry; empty while the material file is not generated yet."
    if not os.path.exists(MATERIALS_PATH):
        print(f"note: {MATERIALS_PATH} not found -- 0 material entries")
        return []
    with open(MATERIALS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _index_by_core(entries):
    idx = collections.defaultdict(set)
    for key, e in entries.items():
        for frag in {_canon(e.get("name", "")), _core(e.get("name", ""))}:
            if len(frag) >= 2:
                idx[frag].add(key)
    return idx


def _index_by_focus(entries):
    """
    Exact index of the library's `focus` literals.

    A library row's name and the literal it actually exercises often
    differ, so without this index a material entry whose core only appears
    in a row's `focus` looks new and gets merged as a duplicate.

    Deliberately **exact** (canon equality, no substring): the fragments are
    short and a containment test would collide.  Single-character literals
    are skipped, matching ``_index_by_core``; those go through
    ``_EXPLICIT_MATCH`` instead.
    """
    idx = collections.defaultdict(set)
    for key, e in entries.items():
        for f in e.get("focus") or []:
            c = _canon(f)
            if len(c) >= 2:
                idx[c].add(key)
    return idx


def _match(pattern, entries, idx, by_name, focus_idx):
    """
    Return (kind, [library_keys]) for one material pattern.

    Lookup order, first hit wins: alias -> exact -> contain -> focus -> core.
    Only the *material* direction of containment counts (``a in c``): a
    material sometimes names a point more narrowly than the library does, so
    the material's form sits inside a longer library name.

    The reverse (``c in a``) is deliberately **not** used.  It looks
    harmless -- the library's name appears inside the material pattern -- but
    the points in this library are *composites*, so any short row that
    happens to be a substring silently "covers" an unrelated longer point.
    That is exactly how the Korean audit once hid 39 real gaps behind a
    "0 missing" report.  A genuine same-point-different-name case is a
    *judgement*, so it belongs in ``_EXPLICIT_MATCH`` where it is
    reviewable, not in a substring test.
    """
    alias = _EXPLICIT_MATCH.get(pattern)
    if alias is not None and alias in by_name:
        return "covered", sorted(by_name[alias])
    alts = _alts(pattern)
    if not alts:
        return "missing", []
    exact, contain, corehits, focus_hits = set(), set(), set(), set()
    for a in alts:
        for key, e in entries.items():
            c = _canon(e.get("name", ""))
            if not c:
                continue
            if a == c:
                exact.add(key)
            elif len(a) >= 2 and len(c) >= 2 and a in c:
                contain.add(key)
        if len(a) >= 2:
            corehits |= idx.get(a, set())
            focus_hits |= focus_idx.get(a, set())
    if exact:
        return "covered", sorted(exact)
    if contain:
        return "covered", sorted(contain)
    if focus_hits:
        return "covered", sorted(focus_hits)
    if corehits:
        return "supplementable", sorted(corehits)
    return "missing", []


def _gaps(lib_entry):
    gaps = []
    if not (lib_entry.get("zh") or "").strip():
        gaps.append("no-zh")
    if not (lib_entry.get("meaning") or "").strip():
        gaps.append("no-meaning")
    if not lib_entry.get("level"):
        gaps.append("no-level")
    if not lib_entry.get("examples"):
        gaps.append("no-examples")
    return gaps


# ---- hand-written baseline (runtime matcher) ---------------------------
#
# The library JSON holds only the merged rows.  The hand-written rules live
# in the engine module itself (``grammar_analysis_ru._RU_RULES``), so a name
# comparison against the JSON can never see them: a material point such as
# "у меня есть" would be reported as a gap even though ``ru_u_menya``
# already covers it, and merging it would put two rows for one point on the
# panel.
#
# So the baseline is checked the only way that is trustworthy -- run each
# material's own example sentences through the runtime engine and see which
# hand-written rules fire.  That is the same matcher the panel uses, so the
# verdict cannot drift from what a reader sees.  Needs pymorphy3; when it
# is missing the check is skipped **with a warning**, never silently, so a
# false "0 missing" cannot hide behind an absent dependency.

_ENGINE_BASELINE = None


def _engine_baseline():
    "Hand-written rule key -> name, or {} when the engine is unavailable."
    global _ENGINE_BASELINE  # pylint: disable=global-statement
    if _ENGINE_BASELINE is None:
        try:
            from lute.read.render import grammar_analysis_ru as ru
        except ImportError as exc:  # pragma: no cover - environment dependent
            print(f"note: ru engine unavailable ({exc}); hand-written "
                  "baseline NOT checked -- covered points may look like gaps")
            _ENGINE_BASELINE = {}
        else:
            _ENGINE_BASELINE = {r["key"]: r["name"] for r in ru._RU_RULES}
    return _ENGINE_BASELINE


def _engine_hits(entry):
    "Hand-written rule keys that fire on this material entry's own examples."
    baseline = _engine_baseline()
    if not baseline:
        return []
    try:
        from lute.read.render.grammar_analysis_ru import analyze_russian
    except ImportError:  # pragma: no cover - environment dependent
        return []
    hits = set()
    for ex in entry.get("examples") or []:
        sentence = ex.get("russian") if isinstance(ex, dict) else ex
        if not sentence:
            continue
        for found in analyze_russian(sentence, display_lang="en"):
            if found["key"] in baseline:
                hits.add(found["key"])
    return sorted(hits)


def run(write=True, print_summary=False):
    entries = _load_library()
    idx = _index_by_core(entries)
    focus_idx = _index_by_focus(entries)
    by_name = collections.defaultdict(list)
    for key, e in entries.items():
        by_name[e.get("name", "")].append(key)
    materials = _load_materials()
    covered, supplementable, missing, confusables = {}, {}, {}, []
    engine_covered = {}
    n = n_covered = n_suppl = n_missing = 0
    for e in materials:
        n += 1
        engine_keys = _engine_hits(e)
        if engine_keys:
            # A hand-written rule already fires on this point's own
            # sentences -- it is covered, whatever the library says.
            kind, keys = "covered", engine_keys
            engine_covered[e["pattern"]] = engine_keys
        else:
            kind, keys = _match(e["pattern"], entries, idx, by_name, focus_idx)
        if kind == "covered":
            n_covered += 1
            for k in keys:
                if k not in entries:
                    # A hand-written rule: it lives in the engine module, not
                    # in the library JSON, so there is nothing to enlarge.
                    continue
                gaps = _gaps(entries[k])
                if gaps:
                    supplementable.setdefault(
                        k,
                        {
                            "library": {
                                "key": k,
                                "name": entries[k]["name"],
                                "level": entries[k].get("level"),
                            },
                            "from": [],
                            "gaps": gaps,
                        },
                    )
                    supplementable[k]["from"].append(
                        {
                            "material": os.path.basename(MATERIALS_PATH),
                            "pattern": e["pattern"],
                        }
                    )
                else:
                    covered[k] = keys
        elif kind == "supplementable":
            n_suppl += 1
            for k in keys:
                confusables.append(
                    {
                        "material_pattern": e["pattern"],
                        "library": {"key": k, "name": entries[k]["name"]},
                        "reason": "fragment-overlap-only",
                    }
                )
        else:
            n_missing += 1
            missing[e["pattern"]] = {
                "name_ru": e.get("name_ru"),
                "level": e.get("level"),
                "formation": e.get("formation"),
                "meaning_en": e.get("meaning_en"),
                "meaning_zh": e.get("meaning_zh"),
                "examples": e.get("examples", []),
                "focus": e.get("focus") or [],
                "notes": e.get("notes"),
                "source": e.get("source"),
            }
    reports = {
        "audit_ru_missing.json": missing,
        "audit_ru_supplementable.json": supplementable,
        "audit_ru_confusables.json": confusables,
        "audit_ru_engine_covered.json": engine_covered,
    }
    if write:
        if not os.path.isdir(MATERIALS_DIR):
            print(f"note: {MATERIALS_DIR} not found -- reports not written")
        else:
            for name, data in reports.items():
                with open(
                    os.path.join(MATERIALS_DIR, name), "w", encoding="utf-8"
                ) as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    if print_summary:
        print(f"material entries: {n}")
        print(f"library entries:  {len(entries)}")
        print(f"covered:          {len(covered)} ({n_covered} material)")
        print(f"  via hand-written rules: {len(engine_covered)}")
        print(f"supplementable:   {len(supplementable)} ({n_suppl} material)")
        print(f"missing (new):    {len(missing)} ({n_missing} material)")
        print(f"confusable hits:  {len(confusables)}")
    return {
        "total": n,
        "covered": n_covered,
        "supplementable": n_suppl,
        "missing": n_missing,
        "engine_covered": len(engine_covered),
        "confusables": len(confusables),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    res = run(print_summary=args.print)
    if args.check:
        assert res["total"] == (
            res["covered"] + res["supplementable"] + res["missing"]
        ), "unmapped entries remain"
        print("OK: every material entry is mapped exactly once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
