"""Build the Japanese grammar golden set from the curated library rows.

The golden set is the single store of the hand-written rules' example
sentences: the library page serves the same sentences to readers, and the
tests re-run every one of them through its own rule, so a point whose
matcher does not recognise its own example can never ship.

Unlike Spanish / French / German / Russian -- where the golden rows were
written by hand -- the Japanese hand-written rules cover points the curated
library already carries: ``n5.json`` etc. hold translated example
sentences for the same constructions.  Deriving the set from them keeps one
authoritative source per sentence and never invents data.

Selection, per rule:

  * examples that actually fire on the rule (``_match_spans``, the same
    matcher the panel uses), from the row whose own grammar point the rule
    reports first, then from anywhere else in the corpus;
  * a sentence that already carries the most translations wins -- the
    vendored rows have English, the merged material rows have Chinese and
    ``ko_enrichment.json`` has Korean per sentence -- so a Korean panel
    gets a gloss on Japanese examples too;
  * at most two per rule, the shortest first, and a sentence is never
    reused for a second rule (an example of 〜もいいです is not also a
    golden sentence for 〜てはいけません).

``mo-ba-mo`` has no firing example in the corpus, so the script reports it
and leaves it out: the entry is written by hand instead.

Usage::

    python -m scripts.generate_grammar_ja_golden          # report
    python -m scripts.generate_grammar_ja_golden --write  # apply
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from lute.read.render import grammar_analysis_ja as ja  # noqa: E402

OUT = os.path.join(BASE, "lute", "jlpt_data", "grammar_ja_golden.json")
MAX_PER_RULE = 2

# Sentences that fire on a rule but are examples of a different point: これは
# ペンではありません only reaches 〜で because では contains で.  Kept as a
# reviewed drop list rather than a heuristic -- the library rows are the
# authority on what a sentence belongs to, and the generator cannot see it.
_MISFIRES = {
    "de_place_means": ["これはペンではありません。"],
    "suki_kirai": [
        "もう20年ここに住みました。ここが好きだからです。",
        "A「今日はおばあさんのところへ行きますね。おばあさんが好きなお菓子を持って行きましょうか。」 B「いいですね。そうしましょう。」",
    ],
    "to_and_with": ["彼は学生だと思う。"],
    "te_mo_ii_desu": ["明日は学校に来なくてもいいです。"],
}

# The one hand-written rule with no firing example in the corpus, written by
# hand.  Its row is 〜も〜ば〜も, which needs two clauses each ending in ば.
_MO_BA_MO = {
    "name": "〜も〜ば〜も",
    "examples": [
        {
            "japanese": "忙しくもあれば、楽しくもある。",
            "english": "It is busy, but it is also fun.",
            "chinese": "忙是忙，但也很快乐。",
            "ko": "바쁘기도 하지만 즐겁기도 하다.",
        }
    ],
}


def _fragments(text):
    """
    Japanese literals of a title, with the one-kana floor lifted.

    ``_jp_fragments`` drops fragments under two characters -- で and と are
    exactly the literals the particle rules match on, so the floor has to go
    for the ranking alone.
    """
    if not text:
        return set()
    text = str(text).replace("〜", "").replace("～", "")
    out = set()
    for clause in ja._FRAG_SPLIT.split(text):
        frag = ja._PARENS.sub("", clause).strip().strip("。．.　 ")
        if frag and ja._ONLY_JP.match(frag):
            out.add(frag)
    return out


def _rule_literals(rule):
    """
    The literal strings the rule matches, for ranking the rows it reports.

    A hand-written rule is either a regex spec (the literal is the pattern,
    unescaped) or a token spec (the surfaces it requires), so both shapes
    are read back out here.
    """
    literals = set()
    for spec in rule.get("patterns") or []:
        if spec.get("type") == "regex":
            literals.add(spec["re"].pattern.replace("\\", ""))
        for cond in spec.get("conds") or []:
            literals |= set(cond.get("surface") or ())
            literals |= set(cond.get("surface_in") or ())
    return {lit for lit in literals if lit}


def _row_literals(data_rule):
    "The literals a data row is about, from its title or descriptive pattern."
    return _fragments(data_rule.get("pattern")) | _fragments(
        data_rule.get("descriptive")
    )


def _glosses(row, entry_id, ko_examples):
    "Translations for one example row, in the display languages."
    jp = (row.get("japanese") or "").strip()
    zh_examples = (ja._ZH_ENRICH.get(entry_id) or {}).get("examples") or {}
    return {
        "japanese": jp,
        "english": (row.get("english") or "").strip(),
        "chinese": (
            (row.get("chinese") or "").strip() or (zh_examples.get(jp) or "").strip()
        ),
        "ko": (ko_examples.get(jp) or "").strip(),
    }


def collect():
    "The golden set, plus the rules that still have no example."
    data = ja._get_data_rules()
    ko_examples = {
        entry_id: (ja._KO_ENRICH.get(entry_id) or {}).get("examples") or {}
        for entry_id in set(d.get("entry_id") for d in data)
    }

    golden = {}
    used = set()
    for rule in ja._N5_RULES + ja._CONSTRUCTION_RULES:
        literals = _rule_literals(rule)
        dropped = set(_MISFIRES.get(rule["key"]) or [])
        candidates = []
        for order, data_rule in enumerate(data):
            # A sentence that fires on a *different* row's construction is
            # not an example of this point: これはペンではありません fires
            # on 〜で only because では contains で.  Ranking the rows the
            # rule shares a literal with first keeps such misfires out.
            shared = bool(literals & _row_literals(data_rule))
            entry_id = data_rule.get("entry_id") or ""
            for row in data_rule.get("example_rows") or []:
                jp = (row.get("japanese") or "").strip()
                if not jp or jp in used or jp in dropped:
                    continue
                if not ja._match_spans(rule, ja._tokens_for(jp), jp):
                    continue
                gloss = _glosses(row, entry_id, ko_examples[entry_id])
                score = (
                    0 if shared else 1,
                    order,
                    -sum(1 for f in ("english", "chinese", "ko") if gloss[f]),
                    # A dialogue turn pair makes a poor card illustration, so
                    # past a readable length the shorter sentence wins even
                    # with one translation fewer.
                    max(0, len(jp) - 40),
                    len(jp),
                )
                candidates.append((score, gloss))
        candidates.sort(key=lambda pair: pair[0])
        chosen = [gloss for _score, gloss in candidates[:MAX_PER_RULE]]
        if chosen:
            golden[rule["key"]] = {"name": rule["pattern"], "examples": chosen}
            used.update(gloss["japanese"] for gloss in chosen)
    golden["mo_ba_mo"] = _MO_BA_MO
    missing = [
        rule["key"]
        for rule in ja._N5_RULES + ja._CONSTRUCTION_RULES
        if rule["key"] not in golden
    ]
    return golden, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the golden file")
    args = parser.parse_args()

    golden, missing = collect()
    for key, entry in golden.items():
        print(f"{key}:")
        for example in entry["examples"]:
            tags = "".join(
                "+" if example[field] else "-" for field in ("english", "chinese", "ko")
            )
            print(f"    [{tags}] {example['japanese']}")
    sentences = sum(len(e["examples"]) for e in golden.values())
    print(f"\n{len(golden)} rules, {sentences} sentences")
    if missing:
        print("no firing example (write by hand): " + ", ".join(missing))
    if args.write:
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(golden, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
