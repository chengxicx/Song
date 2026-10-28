"""Audit the Korean grammar rules for same-sentence / same-span duplicates.

Three sources meet in the Korean panel: the hand-written beginner rules, the
vendored kimchi rows (whose matcher is a regex over each row's ``focus``) and
the pattern-derived rows merged from the study materials (whose matcher is
derived from the row's name).  Two rows naming the same construction, or a
data row whose point a hand-written rule already reports, end up firing on the
same span of the same sentence -- and the panel reports one construction
twice.

This audit finds those collisions the way the German / Russian / Spanish
engines do: every rule is run over the union of *all* rules' own example
sentences, and a pair is reported when both fire with a near-identical span
(IoU >= ``_IOU``) on the same sentence.  Pairs seen on fewer than ``_MIN_HITS``
sentences are dropped as incidental.

The result is the reviewable candidate list behind
``grammar_analysis_ko._DUPLICATE_RULE_IDS``.  A silenced row keeps its library
entry (it is real material); only the reading panel drops it.  This script is
how that list is re-derived when the library grows, instead of being a one-off
judgement.

Usage::

    python -m scripts.audit_grammar_ko_overlaps            # summary
    python -m scripts.audit_grammar_ko_overlaps --json     # machine-readable
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lute.read.render import grammar_analysis_ko as ko  # noqa: E402

# Two spans count as "the same point" when their union overlap reaches this.
# Below it the pair is one construction *nested inside* another (a wide data
# row around a narrow hand-written rule), which is a legitimate report.
_IOU = 0.6

# A collision on a single example sentence is an accident of that sentence;
# a duplicate construction collides across the corpus.  Only pairs seen at
# least this often are treated as duplicates.
_MIN_HITS = 2

_RULES_CACHE = None
_TOKENS = {}


def _audit_rules():
    """
    Every rule in library order -- **including** the rows the panel silences.

    ``_get_all_rules``/``_get_pattern_rules`` return the full sets on purpose:
    the panel drops ``_DUPLICATE_RULE_IDS`` later, and auditing only the
    loaded set could never re-derive that list.
    """
    global _RULES_CACHE  # pylint: disable=global-statement
    if _RULES_CACHE is None:
        _RULES_CACHE = (
            ko._KO_RULES  # pylint: disable=protected-access
            + ko._load_data_rules()  # pylint: disable=protected-access
            + ko._load_pattern_rules()  # pylint: disable=protected-access
        )
    return _RULES_CACHE


def _tokens(sentence):
    "Tokenise once per sentence: the audit runs ~370 rules over each."
    if sentence not in _TOKENS:
        _TOKENS[sentence] = ko._tokens_for(sentence)  # pylint: disable=protected-access
    return _TOKENS[sentence]


def audit_corpus():
    "The union of every rule's own example sentences."
    sentences = set()
    for rule in _audit_rules():
        for example in rule.get("examples") or []:
            text = (
                example.get("korean") if isinstance(example, dict) else example
            ) or ""
            text = text.strip()
            if text:
                sentences.add(text)
    for (
        examples
    ) in ko._PATTERN_RULE_EXAMPLES.values():  # pylint: disable=protected-access
        for text in examples:
            text = (text or "").strip()
            if text:
                sentences.add(text)
    return sorted(sentences)


def _iou(a, b):
    (s1, e1), (s2, e2) = a, b
    inter = max(0, min(e1, e2) - max(s1, s2))
    union = max(e1, e2) - min(s1, s2)
    return inter / union if union else 0.0


def overlapping_pairs():
    "key-pair -> sentences on which the two rules share a near-identical span."
    rules = _audit_rules()
    by_key = {r["key"]: r for r in rules}
    pairs = collections.defaultdict(list)
    for sentence in audit_corpus():
        tokens = _tokens(sentence)
        spans = {}
        for rule in rules:
            found = ko._match_spans(  # pylint: disable=protected-access
                rule, tokens, sentence
            )
            if found:
                spans[rule["key"]] = found
        keys = sorted(spans)
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                if any(_iou(x, y) >= _IOU for x in spans[a] for y in spans[b]):
                    pairs[(a, b)].append(sentence)
    return by_key, pairs


def candidates(min_hits=_MIN_HITS):
    """
    The dedupe decisions, as (dropped_key, kept_key, hits, kind) tuples.

    A data row colliding with a hand-written rule is dropped in favour of the
    rule; two data rows colliding keep whichever comes first in library order,
    which is the order the panel would otherwise show them in, so the decision
    is stable across re-runs.  A pair of hand-written rules is reported but
    never acted on: those are the broad beginner forms and their overlap
    predates the merged rows.

    The hand-written pass runs first so the result is a fixpoint rather than a
    chain: several data rows may all collide with one hand-written rule and
    each also collide with its own twin -- deciding the twins first would
    "keep" a row that is itself silenced.
    """
    by_key, pairs = overlapping_pairs()
    order = [r["key"] for r in _audit_rules()]
    hand = {r["key"] for r in ko._KO_RULES}  # pylint: disable=protected-access

    graph = collections.defaultdict(dict)
    for (a, b), sentences in pairs.items():
        if len(sentences) < min_hits:
            continue
        if a in hand and b in hand:
            continue  # pre-existing, left alone
        if by_key[a]["pattern"] == by_key[b]["pattern"]:
            # Same surface, different sense (하고 = "and" / "with"; 마다 =
            # "every" / "each"): the panel merges rules by display name, so
            # the reader already sees one entry with both senses.
            continue
        graph[a][b] = max(graph[a].get(b, 0), len(sentences))
        graph[b][a] = max(graph[b].get(a, 0), len(sentences))

    decisions = {}
    # Pass 1: anything colliding with a hand-written rule is covered by it.
    for key in sorted(graph):
        if key in hand:
            continue
        hits = [(n, h) for n, h in graph[key].items() if n in hand]
        if hits:
            n, h = max(hits, key=lambda kv: kv[1])
            decisions[key] = (n, h, "hand-written")
    # Pass 2: among the survivors keep the earliest in library order.
    for key in sorted(graph, key=lambda k: order.index(k)):
        if key in hand or key in decisions:
            continue
        earlier = [
            (n, h)
            for n, h in graph[key].items()
            if n not in hand
            and n not in decisions
            and order.index(n) < order.index(key)
        ]
        if earlier:
            n, h = max(earlier, key=lambda kv: kv[1])
            decisions[key] = (n, h, "merged twin")
    return by_key, sorted(decisions.items())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--min-hits", type=int, default=_MIN_HITS, help="minimum shared sentences"
    )
    args = ap.parse_args()

    by_key, cands = candidates(args.min_hits)
    if args.json:
        json.dump(
            [
                {
                    "drop": d,
                    "keep": k,
                    "hits": h,
                    "kind": kind,
                    "name": by_key[d]["pattern"],
                }
                for d, (k, h, kind) in cands
            ],
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        print()
        return 0

    print(f"{len(cands)} row(s) to silence in the panel:")
    for dropped, (kept, hits, kind) in cands:
        print(f"  x{hits:<3d} {dropped}")
        print(f"        dupe of {kept} ({kind}): {by_key[dropped]['pattern'][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
