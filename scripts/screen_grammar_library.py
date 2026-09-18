"""Screen the vendored JLPT grammar library for entries that are vocabulary.

The panel is a *grammar* panel: a learner clicks a sentence to see the
constructions in it.  Lute already answers "what does 時間 mean" from the word
popup, so an entry whose row only repeats that -- "〜時間 = ……小时" -- adds a
row and no grammar.

The library is a verbatim copy of a curated JLPT list (see
``lute/jlpt_data/grammar/ATTRIBUTION.md``) and a slice of it is really
vocabulary: counters, calendar words, lexical adverbs.  Which entries those
are is decided in the engine by ``_VOCAB_IDS``; this script is how that list
is produced, so it can be re-derived when the library is updated instead of
being a one-off judgement.

Two properties are read off the entry's own data, both required:

  A. the descriptive ``pattern`` carries no conjugation / attachment slot
     -- no "Plain form", "Verb-て form", "ます-stem", "dictionary form", ....
     A pattern like ``<lexical class> + <word>`` teaches a word plus its
     typical collocates; a pattern with a form slot teaches how a form
     attaches to a base.

  B. the spec derived at load time degenerated to a bare literal, and that
     literal starts with a content word (名詞 / 動詞 / 形容詞 / 副詞 /
     代名詞 / 連体詞) rather than a functional morpheme (助詞 / 助動詞 /
     接尾辞 / 感動詞 / 接続詞 / 形状詞).

The screen is deliberately high-recall: it is the candidate list a human then
reviews, not the decision.  It cannot tell 時間 from こと (both 名詞), so it
flags every bare-noun formality, and it misses entries whose pattern names a
concept ("Transitive verb vs intransitive verb pairs").  Everything it returns
must be classified by hand -- see the groups in ``_VOCAB_IDS``.

Usage::

    python -m scripts.screen_grammar_library
    python -m scripts.screen_grammar_library --corpus /tmp/ja_corpus.hex

Passing ``--corpus`` (one hex-encoded page per line) adds the page share each
rule actually reaches, which is what makes the noisy ones obvious.
"""

import argparse
import binascii
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lute.read.render import grammar_analysis_ja as G  # noqa: E402

_GRAMMAR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lute", "jlpt_data", "grammar",
)

# A pattern segment mentioning any of these describes how a *form* attaches.
# Word boundaries matter: "stem" also matches "system" ("何曜日 / 曜日 system"),
# which would hide a real candidate.
_FORM_SLOT = re.compile(
    r"\bplain\b|\bdictionary form\b|ます-stem|\bstem\b|\bV\b|\bVerb\b|"
    r"て form|て-form|ない-form|\bnegative form\b|\bvolitional\b|"
    r"\bpotential\b|\bpassive\b|\bcausative\b|non-past|\bpast\b|"
    r"\badverbial\b|\battributive\b|\bmodifying\b",
    re.I,
)

# Token classes that make a literal a word rather than a grammatical marker.
_CONTENT_POS = {"名詞", "動詞", "形容詞", "副詞", "代名詞", "連体詞"}

_JP = re.compile(r"[\u3041-\u309f\u30a1-\u30fa\uff66-\uff9f\u4e00-\u9fff]")


def load_library():
    "id -> entry, across every level file."
    lib = {}
    for path in sorted(glob.glob(os.path.join(_GRAMMAR_DIR, "n*.json"))):
        with open(path, encoding="utf-8") as fh:
            for item in json.load(fh):
                lib[item["id"]] = item
    return lib


def split_pattern(pattern):
    """Classify each '+'-separated segment of a descriptive pattern.

    Returns a list of (kind, text) with kind in {"slot", "mixed", "literal"}:
    "slot" is a bare latin class name (``Noun``, ``Plain form``), "mixed" a
    segment naming both a class and a Japanese form (``Verb-て form``), and
    "literal" a segment that is the Japanese material itself.
    """
    out = []
    for part in re.split(r"\s*[+/]\s*", pattern):
        part = part.strip()
        if not part:
            continue
        has_jp = bool(_JP.search(part))
        has_latin = bool(re.search(r"[A-Za-z]", part))
        if not has_jp:
            out.append(("slot", part))
        elif has_latin:
            out.append(("mixed", part))
        else:
            out.append(("literal", part))
    return out


def has_form_slot(pattern):
    "True when the pattern says how a conjugation form attaches (property A)."
    for kind, seg in split_pattern(pattern):
        if kind in ("slot", "mixed") and _FORM_SLOT.search(seg):
            return True
    return False


def spec_literals(rule):
    """The literals a rule's derived spec matches, or None if it is not
    literal-only (a class-based condition means the derivation kept a slot).

    Reads "derived" rather than "patterns": a rule that is already skipped on
    review has an empty "patterns" by design, and the screen has to keep
    re-deriving the same verdict from the entry itself.
    """
    lits = []
    for spec in rule.get("derived") or rule["patterns"]:
        if spec.get("type") == "regex":
            lits.append(spec["re"].pattern)
        elif spec.get("type") == "tokens":
            for cond in spec.get("conds", []):
                if cond.get("lemma"):
                    lits.extend(sorted(cond["lemma"]))
                elif cond.get("surface"):
                    lits.append(cond["surface"])
                else:
                    return None
        else:
            return None
    return lits or None


def literal_head_pos(literal):
    "First-token POS of a literal, or None when it does not tokenise."
    tokens = G._tokens_for(literal)
    return tokens[0]["pos"][0] if tokens else None


def screen():
    """The candidates, as (id, rule, entry, literals, head_pos) tuples."""
    derived = {r["key"][3:]: r for r in G._DATA_RULES if r["key"].startswith("ds_")}
    lib = load_library()
    out = []
    for rid, rule in sorted(derived.items()):
        entry = lib.get(rid)
        if entry is None or has_form_slot(entry["pattern"]):
            continue
        literals = spec_literals(rule)
        if not literals:
            continue
        heads = {literal_head_pos(lit) for lit in literals}
        if not heads or heads - _CONTENT_POS:
            continue
        out.append((rid, rule, entry, literals, heads))
    return out


def corpus_page_share(path):
    """rule key -> share of pages it fires on (needs the corpus file)."""
    pages = [
        binascii.unhexlify(line.strip()).decode("utf-8")
        for line in open(path, encoding="utf-8")
        if line.strip()
    ]
    hits = collections.Counter()
    for page in pages:
        text = page.replace("\u200b", "")
        for entry in G.analyze_japanese(text, display_lang="zh"):
            hits[entry["key"]] += 1
    return {k: 100.0 * v / len(pages) for k, v in hits.items()}, len(pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", help="hex-encoded pages, one per line")
    args = ap.parse_args()

    share, npages = ({}, 0)
    if args.corpus:
        share, npages = corpus_page_share(args.corpus)

    cands = screen()
    print("%d candidates%s"
          % (len(cands), " (corpus: %d pages)" % npages if npages else ""))
    print()
    print("%-6s %-30s %-5s %-30s %s" % ("页%", "id", "lvl", "规格字面量", "原料 pattern"))
    rows = []
    for rid, rule, entry, literals, heads in cands:
        pct = share.get(rule["key"], 0.0)
        rows.append((pct, rid, rule, entry, literals))
    for pct, rid, rule, entry, literals in sorted(rows, reverse=True):
        print("%-6s %-30s %-5s %-30s %s"
              % ("%.1f" % pct if npages else "-", rid, rule["level"],
                 "/".join(literals)[:30], entry["pattern"][:44]))
    print()
    in_vocab = sorted(r for r in (x[1] for x in rows) if r in G._VOCAB_IDS)
    print("已在 _VOCAB_IDS：%d 条" % len(in_vocab))
    for rid in in_vocab:
        print("   ", rid)


if __name__ == "__main__":
    main()
