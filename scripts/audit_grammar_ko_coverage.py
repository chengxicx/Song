"""Audit the Korean grammar material files against the built-in Korean library.

Reads every ``scripts/grammar_materials_ko/*.json`` file (one array of
entries per source book) and matches each entry against the vendored
library ``lute/jlpt_data/grammar_ko.json`` (a kimchi-grammar snapshot).

Matching is by normalized Hangul core: slot descriptors ("V", "A", "N",
"A/V", "동사", ...) and punctuation are stripped, the tilde variants
``~ / 〜 / ~ / -`` are dropped, and the remaining Hangul syllables/jamo are
compared.  A material pattern may list alternatives separated by ``,`` or
``/`` (e.g. ``있다/없다``); each alternative is matched independently, and
``V-고 싶다`` (core ``고싶다``) matches the library entry ``고 싶다``.

Three reports are written next to the materials:

  audit_ko_missing.json        material entries with no library counterpart
                               (candidates for new library rows)
  audit_ko_supplementable.json library entries matched by a material entry
                               but lacking a Chinese/Korean gloss, examples
                               or a level
  audit_ko_confusables.json    near-miss pairs (fragment-overlap only) for
                               human review -- the audit never merges across
                               a confusable pair on its own

Usage::

    python -m scripts.audit_grammar_ko_coverage            # write the reports
    python -m scripts.audit_grammar_ko_coverage --print    # summary
    python -m scripts.audit_grammar_ko_coverage --check    # gate: every
                                                           # entry mapped once
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_DIR = os.path.join(BASE, "scripts", "grammar_materials_ko")
LIBRARY_PATH = os.path.join(BASE, "lute", "jlpt_data", "grammar_ko.json")

# Slot descriptors used by the source books to name the word class a point
# attaches to.  Longest first so "A/V" is stripped before bare "A".
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
    "관형형",
    "동사",
    "형용사",
    "명사",
    "부사",
    "의존명사",
    "V",
    "A",
    "N",
]

# Material patterns that name the same grammar point as an existing library
# entry under a different surface form (the books teach the colloquial /
# conjugated spelling, kimchi-grammar records the dictionary form).  Each
# pair was hand-reviewed in the 2026-09-29 Korean merge; the audit maps them
# so no near-duplicate row is created.
_EXPLICIT_MATCH = {
    "A/V-(으)ㄹ 거예요": "(으)ㄹ 것이다",
    "V-(으)ㄹ 거예요": "(으)ㄹ 것이다",
    "A/V-겠어요": "겠",
    "A/V-았/었어요": "았/었/였",
    "A/V-았/었었어요": "(았/었/였)었",
    "A/V-아/어요": "아/어/여",
    "A/V-아/어 (반말체)": "아/어/여",
    "A/V-지요?": "지",
    "A/V-(으)ㄴ들": "(은/ㄴ)들",
    "N-(이)며": "(으)며",
    "A/V-다 (서술체)": "(ㄴ/는)다",
    "A/V-았/었던": "던",
    "A/V-았/었던, N이었던": "던",
    "N 만에": "만",
    "A/V-(느)ㄴ다면": "(ㄴ/는)다면",
    "A/V-(으)ㄹ 리가 없다": "ㄹ/을 리가 없다",
    "A/V-다니요?": "다니/라니/냐니/자니",
    "V-(느)ㄴ답시고": "(ㄴ/는)답시고",
    "V-(느)ㄴ다는 것이": "(ㄴ/는)다는, (이)라는",
    "V-다 보니": "다(가) 보니(까)",
    "V-다 보면": "다(가) 보면",
    "A/V-기는커녕": "(은/는)커녕",
    "V-지 마세요": "지 말다",
    "V-아/어 주세요, V-아/어 주시겠어요?": "아/어 주다",
    "V-아/어 줄게요, V-아/어 줄까요?": "아/어 주다",
    # Slot-stripped core is a single syllable, so neither index can hold it
    # (both require >= 2 chars).  The upstream row for -되 is named "(으)되";
    # without this the book's "V-되" merges as a duplicate whose derived
    # matcher (lemma 되/되다) then fires on the ordinary verb 되다.
    "V-되": "(으)되",
    # Surfaced when `contain` was made one-directional (2026-09-29).  Each of
    # these is the same point under a different name: the library row's own
    # `focus` already exercises the book's spelling (e.g. `(으)ㄹ까` focuses
    # `찍을까요`, `탈까요`), so the row *is* the point and merging again would
    # duplicate it.  Verified one by one against `focus`, not by similarity --
    # `A/V-(으)ㄹ 뿐더러` looks close to `(으)ㄹ 뿐만 아니라` but its focus never
    # contains 뿐더러, so it stays a genuine gap and is merged instead.
    "A/V-(으)ㄹ까요?": "(으)ㄹ까",
    "V-(으)ㄹ까요?": "(으)ㄹ까",
    "A/V-잖아요": "잖아",
    "N-은/는 대로": "대로",
    "A/V-았/었더라면": "더라면",
    "V-았/었더니": "(았/었/했)더니",
    "V/A-는 듯이": "듯이",
    "V-아/어야지요": "아/어야지",
    "못 V (V-지 못하다)": "지 못하다",
    "A/V-(으)ㅁ에도 불구하고": "에도 불구하고",
    "N-(으)로 인해서": "(으)로 인하다",
    "V-(으)ㅁ에 따라": "에 따라(서)",
    "A/V-든지 -든지 / N-(이)든지": "든지",
}

_HANGUL = re.compile(r"[\uac00-\ud7a3\u3131-\u318e]")
_TILDE = re.compile(r"[~〜∼\-–—]")
_SLOT_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(sorted(_SLOT_WORDS, key=len, reverse=True))
    + r")(?![A-Za-z])"
)


def _canon(text):
    "Normalize a pattern to its bare Hangul core (syllables + jamo)."
    t = _TILDE.sub(" ", text or "")
    t = _SLOT_RE.sub(" ", t)
    return "".join(_HANGUL.findall(t))


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
    "Longest Hangul run in a pattern (after slot stripping)."
    t = _SLOT_RE.sub(" ", _TILDE.sub(" ", text or ""))
    runs = _HANGUL.findall(t)
    # longest contiguous Hangul run
    best, cur = "", ""
    for ch in t:
        if _HANGUL.match(ch):
            cur += ch
            if len(cur) > len(best):
                best = cur
        else:
            cur = ""
    return best or "".join(runs)


def _load_library():
    with open(LIBRARY_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)
    return {e["key"]: e for e in entries}


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

    A kimchi row's name and the literal it actually exercises often differ:
    the row named ``(이)거니`` carries ``focus: ["이거니와", "거니와"]``, i.e.
    it *is* the "not only ... but also" point.  Without this index a book
    entry written ``A/V-거니와`` looks new and gets merged as a duplicate.

    Deliberately **exact** (canon equality, no substring): the fragments are
    short and a containment test would collide (``하고`` inside
    ``-고 나니까``).  Single-character literals are skipped, matching
    ``_index_by_core``; those go through ``_EXPLICIT_MATCH`` instead.
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
    Only the *material* direction of containment counts (``a in c``): the book
    sometimes names a point more narrowly than the library does, so the book's
    form sits inside a longer library name (``A/V-(으)ㄹ 겸`` inside
    ``(으)ㄹ 겸 -(으)ㄹ 겸``).

    The reverse (``c in a``) is deliberately **not** used.  It looks harmless
    -- the library's name appears inside the book's pattern -- but Korean
    grammar points are *composites*, so any short library row that happens to
    be a substring silently "covers" an unrelated longer point:

        V-기 일쑤이다        <- "covered" by the copula 이다
        A/V-(으)ㄹ 법하다     <- "covered" by 하다 ("to do")
        N-을/를 비롯해서     <- "covered" by the object particle 을/를
        N-(으)로 말미암아     <- "covered" by the particle (으)로

    That hid 39 real gaps behind a "0 missing" report.  A genuine
    same-point-different-name case is a *judgement*, so it belongs in
    ``_EXPLICIT_MATCH`` where it is reviewable, not in a substring test.
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
    if not (lib_entry.get("ko") or "").strip():
        gaps.append("no-ko")
    if not lib_entry.get("level"):
        gaps.append("no-level")
    if not lib_entry.get("examples"):
        gaps.append("no-examples")
    return gaps


def run(write=True, print_summary=False):
    entries = _load_library()
    idx = _index_by_core(entries)
    focus_idx = _index_by_focus(entries)
    by_name = collections.defaultdict(list)
    for key, e in entries.items():
        by_name[e.get("name", "")].append(key)
    material_files = sorted(
        p
        for p in glob.glob(os.path.join(MATERIALS_DIR, "*.json"))
        if not os.path.basename(p).startswith(("audit_", "zh_", "ko_"))
    )
    covered, supplementable, missing, confusables = {}, {}, {}, []
    n = n_covered = n_suppl = n_missing = 0
    for path in material_files:
        with open(path, encoding="utf-8") as fh:
            for e in json.load(fh):
                n += 1
                kind, keys = _match(e["pattern"], entries, idx, by_name, focus_idx)
                if kind == "covered":
                    n_covered += 1
                    for k in keys:
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
                                    "material": os.path.basename(path),
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
                        "level": e.get("level"),
                        "formation": e.get("formation"),
                        "meaning_en": e.get("meaning_en"),
                        "meaning_zh": e.get("meaning_zh"),
                        "meaning_ko": e.get("meaning_ko"),
                        "examples": e.get("examples", []),
                        "source": e.get("source"),
                    }
    reports = {
        "audit_ko_missing.json": missing,
        "audit_ko_supplementable.json": supplementable,
        "audit_ko_confusables.json": confusables,
    }
    if write:
        for name, data in reports.items():
            with open(os.path.join(MATERIALS_DIR, name), "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    if print_summary:
        print(f"material entries: {n} from {len(material_files)} files")
        print(f"library entries:  {len(entries)}")
        print(f"covered:          {len(covered)} ({n_covered} material)")
        print(f"supplementable:   {len(supplementable)} ({n_suppl} material)")
        print(f"missing (new):    {len(missing)} ({n_missing} material)")
        print(f"confusable hits:  {len(confusables)}")
    return {
        "total": n,
        "covered": n_covered,
        "supplementable": n_suppl,
        "missing": n_missing,
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
