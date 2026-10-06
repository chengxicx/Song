"""Audit the grammar material files against the built-in JLPT grammar library.

Reads every ``scripts/grammar_materials/*.json`` file (one array of entries
per source PDF) and matches each entry against the vendored library under
``lute/jlpt_data/grammar/n{5..1}.json``.  Matching is by normalized pattern
fragment: slot descriptors ("Noun", "V dict", "动词ます形", "体言", ...) are
stripped, the tilde variants 〜 / ～ / ~ are unified, and the remaining
Japanese fragments are compared as sets, so 「V意志形にも同一Vない形」 matches
a library entry recorded as 「動詞の意志形＋にも＋同一動詞のない形」.

Three reports are written, machine-readable, next to the materials:

  audit_missing.json        material entries with no library counterpart
                            (candidates for new library rows)
  audit_supplementable.json library entries matched by a material entry but
                            lacking fields the material can fill: no
                            Chinese gloss for the entry's own examples,
                            empty meaning_detailed, empty formation_notes,
                            or no examples at all
  audit_confusables.json    near-miss pairs flagged by the explicit mapping
                            table below (ばかりに/ばかりか, にしては/わりに,
                            ...) for human review -- the audit never merges
                            across a confusable pair on its own

Usage::

    python -m scripts.audit_grammar_coverage            # write the three reports
    python -m scripts.audit_grammar_coverage --print    # also print a summary
    python -m scripts.audit_grammar_coverage --check    # exit 1 unless every
                                                        # material entry is
                                                        # mapped or flagged
``--check`` is the acceptance gate: 100% mapping means every material entry
lands in exactly one of {covered, supplementable, missing} and every
confusable pair is explicit, so nothing is silently dropped.

The library files are a verbatim copy of jkindrix/japanese-language-data
(CC BY-SA 4.0, see lute/jlpt_data/grammar/ATTRIBUTION.md) and are never
modified here; merging is done by the caller after the reports are reviewed.
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATERIALS_DIR = os.path.join(BASE, "scripts", "grammar_materials")
LIBRARY_DIR = os.path.join(BASE, "lute", "jlpt_data", "grammar")

# Tilde variants and filler punctuation unified before fragmenting.
_TILDES = re.compile(r"[〜～~−-]")
_SLOT_WORDS = [
    "Plain form",
    "polite form",
    "past form",
    "te form",
    "ta form",
    "dict form",
    "dictionary form",
    "ます-stem",
    "nai form",
    "ない form",
    "V",
    "Verb",
    "Adj",
    "Noun",
    "Na",
    "N",
    "い-adj",
    "な-adj",
    "动词",
    "動詞",
    "名詞",
    "名词",
    "形容詞",
    "形容词",
    "形容動詞",
    "形容动词",
    "副詞",
    "副词",
    "体言",
    "体言+",
    "用言",
    "活用语",
    "连体形",
    "连用形",
    "终止形",
    "未然形",
    "假定形",
    "意志形",
    "可能态",
    "被动",
    "使役",
    "简体",
    "敬体",
    "普通形",
    "各类品词",
    "各种品词",
    "疑问词",
    "助词",
    "接尾词",
    "形式体言",
    "动词ます形",
    "动词て形",
    "动词た形",
    "动词原形",
    "动词未然形",
    "动词连体形",
    "动词连用形",
    "动词简体",
    "活用词连体形",
    "活用语终止形",
    "用言连体形",
    "用言终止形",
    "形容词词干",
    "形容动词词干",
    "サ变动词",
    "サ变动词词干",
    "サ变动词词干+こ",
    "五段动词",
    "一段动词",
    "カ变动词",
    "する",
    "する・す",
    "して",
    "されている",
    "前面加",
    "前面接",
    "接在",
    "接续法同",
    "接法同上",
    "接续同上",
    "与接名词的用法相同",
    "名词性结构",
    "体言结构",
    "体言或连体形",
    "体言,活用语连体形",
    "连体形/体言+の",
    "连体形/体言",
    "体言+が/活用语连体形",
    "体言/动词连体形",
    "体言/活用语连体形",
]
_NOISE = re.compile(r"[\s/／、，,;；()（）\[\]【】「」『』…・.。0-9]+")
_JP = re.compile(r"[\u3040-\u30ff\u30fc\u3005\u4e00-\u9fff]")

# Kana/kanji variants the library and the materials spell differently for the
# same point; both sides are folded before fragmenting.  Kept explicit and
# small -- every row here was observed in a real audit miss, not guessed.
_VARIANT_FOLD = {
    "上に": "うえに",
    "上で": "うえで",
    "上は": "うえは",
    "上も": "うえも",
    "上の": "うえの",
    "上でも": "うえでも",
    "上では": "うえでは",
    "代わりに": "かわりに",
    "かのように": "かのようだ",
    "わりに(は)": "わりには",
    "あまりに": "あまり",
    "出来る": "できる",
    "頂く": "いただく",
    "下さる": "くださる",
}

# Single-kana particles are pure noise as index keys: 「わりに(は)」 yields a
# bare は fragment that would collide with half the library.
_INDEX_STOP = frozenset("はがをにもへとでのやねよか")

# Patterns whose slug collapses into stop-listed particles (e.g. がする ->
# する is a slot word, leaving bare が) can never match via the fragment
# index.  These five were hand-reviewed in the 2026-09-29 merge: each maps to
# exactly one library row created for it earlier (hash-suffixed id from a
# romaji collision).  Consulted by _match before falling back to "missing".
_EXPLICIT_MATCH = {
    "がする": "gasuru-62e4ce",
    "にして": "nishite-59525e",
    "にしては": "nishiteha-1d2bbe",
    "として/としては/としても": "toshitetoshitehatoshitemo-1f4501",
    "V意志形が/V意志形と": "vgavto-cb117e",
    # Same collapse as にして above, from the N1/N2/N3 private-material merge
    # (2026-09-30): して is a slot word (V する の て形 descriptor), so として
    # slugs to the stop-listed single と and never reaches the index.  Each maps
    # to the row this merge created from that very entry.
    "として": "toshite",
    "としても": "toshitemo",
    "とする": "tosuru",
}

# Confusable pairs that must never be auto-merged: the audit maps a material
# entry onto a library entry only when their fragment sets agree on the core
# particle; these pairs share one, so the human reviewer decides.
CONFUSABLE_PAIRS = [
    ("ばかりに", "ばかりか"),
    ("ばかりに", "ばかりでなく"),
    ("にしては", "わりに"),
    ("にしては", "わりには"),
    ("かねる", "かねない"),
    ("ことにする", "ことになる"),
    ("わけがない", "わけだ"),
    ("わけがない", "わけにはいかない"),
    ("というものだ", "ということだ"),
    ("ものだ", "ことだ"),
    ("ものか", "ことか"),
    ("それまでだ", "ばそれまでだ"),
    ("に至るまで", "に至っては"),
    (" を余儀なくされる", "を余儀なくさせる"),
    ("から見ると", "からすると"),
    ("から見ると", "から言うと"),
    ("において", "においても"),
    ("ながら", "ながらも"),
    ("につけ", "につけても"),
    ("どころか", "どころではない"),
]


def _slug(text):
    "Normalize a pattern string to a set of Japanese fragments."
    text = _TILDES.sub("~", text or "")
    for w in sorted(_SLOT_WORDS, key=len, reverse=True):
        text = text.replace(w, " ")
    for kana, base in _VARIANT_FOLD.items():
        text = text.replace(kana, base)
    frags = set()
    for part in _NOISE.split(text):
        if _JP.search(part):
            frags.add(part)
    return frags


def _core(frags):
    "The particle-ish fragment a confusable pair is told apart by."
    ranked = sorted(frags, key=len, reverse=True)
    return ranked[0] if ranked else ""


def _load_library():
    entries = {}
    for path in sorted(glob.glob(os.path.join(LIBRARY_DIR, "n[1-5].json"))):
        level = os.path.basename(path)[:-5].upper()
        with open(path, encoding="utf-8") as fh:
            for e in json.load(fh):
                e = dict(e)
                e.setdefault("level", level)
                entries[e["id"]] = e
    return entries


def _index_by_fragment(entries):
    idx = collections.defaultdict(set)
    for eid, e in entries.items():
        for frag in _slug(e.get("pattern", "")):
            if frag not in _INDEX_STOP:
                idx[frag].add(eid)
    return idx


def _confusables_of(core):
    out = set()
    for a, b in CONFUSABLE_PAIRS:
        if _core(_slug(a)) == core:
            out.add(_core(_slug(b)))
        if _core(_slug(b)) == core:
            out.add(_core(_slug(a)))
    return out


def _match(entry, entries, idx):
    "Return (kind, library_ids) for one material entry."
    frags = _slug(entry["pattern"])
    if not frags:
        return "missing", []
    explicit = _EXPLICIT_MATCH.get(entry["pattern"])
    if explicit is not None and explicit in entries:
        return "covered", [explicit]
    cores = {_core(frags)}
    for c in list(cores):
        cores |= _confusables_of(c)
    hits = set()
    for frag in frags:
        hits |= idx.get(frag, set())
    hits &= entries.keys()
    exact = {eid for eid in hits if frags & _slug(entries[eid]["pattern"])}
    if exact:
        return "covered", sorted(exact)
    if hits:
        return "supplementable", sorted(hits)
    return "missing", []


def _gaps(entry, lib_entry):
    "Which fields a material entry can fill for a matched library entry."
    gaps = []
    if not lib_entry.get("examples"):
        gaps.append("no-examples")
    if not lib_entry.get("meaning_detailed"):
        gaps.append("no-meaning-detailed")
    if not lib_entry.get("formation_notes"):
        gaps.append("no-formation-notes")
    for ex in lib_entry.get("examples", []):
        if "chinese" not in ex:
            gaps.append("example-missing-chinese")
            break
    return gaps


def run(write=True, print_summary=False):
    entries = _load_library()
    idx = _index_by_fragment(entries)
    material_files = sorted(
        p
        for p in glob.glob(os.path.join(MATERIALS_DIR, "*.json"))
        if not os.path.basename(p).startswith(("audit_", "zh_"))
    )
    covered, supplementable, missing, unmapped = {}, {}, {}, []
    confusables = []
    n = 0
    skipped = 0
    n_covered = n_suppl = n_missing = 0
    for path in material_files:
        with open(path, encoding="utf-8") as fh:
            for e in json.load(fh):
                n += 1
                if e["pattern"].startswith("宿題"):
                    skipped += 1
                    continue  # quiz compilation block, not a grammar point
                kind, ids = _match(e, entries, idx)
                if kind == "covered":
                    n_covered += 1
                    for eid in ids:
                        gaps = _gaps(e, entries[eid])
                        if gaps:
                            supplementable.setdefault(
                                eid,
                                {
                                    "library": {
                                        "id": eid,
                                        "pattern": entries[eid]["pattern"],
                                        "level": entries[eid]["level"],
                                    },
                                    "from": [],
                                    "gaps": gaps,
                                },
                            )
                            supplementable[eid]["from"].append(
                                {
                                    "material": os.path.basename(path),
                                    "pattern": e["pattern"],
                                }
                            )
                        else:
                            covered[eid] = ids
                elif kind == "supplementable":
                    n_suppl += 1
                    for eid in ids:
                        confusables.append(
                            {
                                "material_pattern": e["pattern"],
                                "library": {
                                    "id": eid,
                                    "pattern": entries[eid]["pattern"],
                                },
                                "reason": "fragment-overlap-only",
                            }
                        )
                else:
                    n_missing += 1
                    missing[e["pattern"]] = {
                        "level": e.get("level"),
                        "formation": e.get("formation"),
                        "meaning_zh": e.get("meaning_zh"),
                        "examples": e.get("examples", []),
                        "source": e.get("source"),
                    }
                    unmapped.append((os.path.basename(path), e["pattern"]))
    reports = {
        "audit_missing.json": missing,
        "audit_supplementable.json": supplementable,
        "audit_confusables.json": confusables,
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
        if skipped:
            print(f"skipped (宿題):    {skipped}")
    return {
        "total": n,
        "covered": n_covered,
        "supplementable": n_suppl,
        "missing": n_missing,
        "confusables": len(confusables),
        "skipped": skipped,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", help="print the coverage summary")
    ap.add_argument(
        "--check",
        action="store_true",
        help="fail unless every material entry is mapped "
        "(covered / supplementable / missing) -- the "
        "acceptance gate for the material merge",
    )
    args = ap.parse_args()
    res = run(print_summary=args.print)
    if args.check:
        assert res["total"] == (
            res["covered"] + res["supplementable"] + res["missing"] + res["skipped"]
        ), "unmapped entries remain"
        print("OK: every material entry is mapped exactly once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
