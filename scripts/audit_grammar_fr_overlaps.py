"""Audit the French grammar rules for same-sentence / same-span duplicates.

The French engine derives a matcher for every merged row from its ``focus``,
so two rows that describe the same construction (or a data row whose point a
hand-written rule already reports) end up firing on the same span of the same
sentence.  The panel would then show two entries for one point.

This audit finds those collisions the way the Spanish / German / Korean
engines do: every rule is run over the union of *all* rules' own example
sentences, and a pair is reported when both fire with a near-identical span
(IoU >= ``_IOU``) on the same sentence.  Pairs seen on fewer than ``_MIN_HITS``
sentences are dropped as incidental -- a one-off overlap on a single example
is noise, not a duplicate construction.

The result is the reviewable candidate list behind
``grammar_analysis_fr._DUPLICATE_RULE_IDS``.  A silenced row stays in
``grammar_fr.json`` and keeps its library entry (it is real material); only
the reading panel drops it.  This script is how that list is re-derived when
the library grows, instead of being a one-off judgement.

Text is normalised the way the engine's entry point normalises it (curly
apostrophe -> ASCII), so the corpus is tokenised exactly as a page would be.

Usage::

    python -m scripts.audit_grammar_fr_overlaps            # summary
    python -m scripts.audit_grammar_fr_overlaps --json     # machine-readable
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lute.read.render import grammar_analysis_fr as fr  # noqa: E402

# Two spans count as "the same point" when their union overlap reaches this.
# Below it the pair is one construction *nested inside* another (a wide data
# row around a narrow hand-written rule), which is a legitimate report, not a
# duplicate.
_IOU = 0.6

# A collision on a single example sentence is an accident of that sentence;
# a duplicate construction collides across the whole corpus.  Only pairs seen
# at least this often are treated as duplicates.
_MIN_HITS = 2

_DATA_PREFIX = "fgm_"

_RULES_CACHE = None


def _audit_rules():
    """
    Every rule in library order -- **including** the rows the panel silences.

    ``_load_data_rules`` returns the full set on purpose: the panel drops
    ``_DUPLICATE_RULE_IDS`` later, and auditing only the surviving set could
    never re-derive that list.
    """
    global _RULES_CACHE  # pylint: disable=global-statement
    if _RULES_CACHE is None:
        _RULES_CACHE = (
            fr._FR_RULES + fr._get_data_rules()
        )  # pylint: disable=protected-access
    return _RULES_CACHE


def _example_texts(rule):
    """
    The rule's own example sentences, normalised like a page would be.

    The golden rows are ``{"fr", "en", "zh", "ko"}`` dicts; the merged data
    rows carry plain strings or ``{"fr", "zh"}`` pairs.  Both shapes reduce
    to the French sentence.
    """
    texts = []
    for example in rule.get("examples") or []:
        if isinstance(example, dict):
            text = example.get("fr") or ""
        else:
            text = example or ""
        text = fr._norm_text((text or "").strip())  # pylint: disable=protected-access
        if text:
            texts.append(text)
    return texts


def audit_corpus():
    "The union of every rule's own example sentences."
    rules = _audit_rules()
    sentences = set()
    for rule in rules:
        sentences.update(_example_texts(rule))
    golden = fr._golden_examples()  # pylint: disable=protected-access
    for rule in rules:
        for entry in golden.get(rule["key"], []):
            text = fr._norm_text((entry.get("fr") or "").strip())  # pylint: disable=protected-access
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
        tokens = fr._tokens_for(sentence)  # pylint: disable=protected-access
        spans = {}
        for rule in rules:
            found = fr.match_rule(rule, tokens, sentence)
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
    rule; two data rows colliding keep whichever comes first in library order
    (``_FR_RULES`` then the merged rows), which is the order the panel would
    otherwise show them in, so the decision is stable across re-runs.  A pair
    of hand-written rules is reported but never acted on: those are the broad
    forms engines and their overlap predates the merged rows.

    The hand-written pass runs first so the result is a fixpoint rather than a
    chain: several data rows may all collide with one hand-written rule and
    each also collide with its own twin -- deciding the twins first would
    "keep" a row that is itself silenced.
    """
    by_key, pairs = overlapping_pairs()
    order = [r["key"] for r in _audit_rules()]

    # key -> {neighbour: hits}, restricted to real duplicate-sized collisions.
    graph = collections.defaultdict(dict)
    for (a, b), sentences in pairs.items():
        if len(sentences) < min_hits:
            continue
        a_data, b_data = a.startswith(_DATA_PREFIX), b.startswith(_DATA_PREFIX)
        if not a_data and not b_data:
            continue  # hand-written vs hand-written: pre-existing, left alone
        graph[a][b] = max(graph[a].get(b, 0), len(sentences))
        graph[b][a] = max(graph[b].get(a, 0), len(sentences))

    def is_hand(key):
        return not key.startswith(_DATA_PREFIX)

    decisions = {}
    # Pass 1: anything colliding with a hand-written rule is covered by it.
    for key in sorted(graph):
        if is_hand(key):
            continue
        hand = [(n, h) for n, h in graph[key].items() if is_hand(n)]
        if hand:
            n, h = max(hand, key=lambda kv: kv[1])
            decisions[key] = (n, h, "hand-written")
    # Pass 2: among the survivors keep the earliest in library order.
    for key in sorted(graph, key=lambda k: order.index(k)):
        if is_hand(key) or key in decisions:
            continue
        earlier = [
            (n, h)
            for n, h in graph[key].items()
            if not is_hand(n)
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
                    "name": by_key[d]["name"],
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
        print(f"        dupe of {kept} ({kind}): {by_key[dropped]['name'][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
