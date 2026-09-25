"""
Decide which terms should be linked to a lemma (dictionary form) parent.

Pure decision logic for the roadmap 2.3 backfill; see
docs/term-parent-lemma-task.md.  There is no database, no session and no
parser here -- the parser is injected as callables -- so the rules can be
unit tested without a sudachi dictionary installed.

Background, i.e. why a backfill is needed at all:

* ``wordparents`` is only written by the manual paths (term form, bulk
  edit, bulk status update), and every one of them goes through
  ``TermRepository._build_db_term()``.
* Terms created when a book is opened come from
  ``Term.create_term_no_parsing()`` and a bare ``session.add()``, so they
  never get a parent.
* ``TermRepository.find_or_new()`` -- the only caller of
  ``Parser.get_lemma()`` -- returns as soon as ``find()`` hits an existing
  term, so its lemma branch never runs for terms that already exist.

Two rules are safety rails rather than language rules: a term that already
has a parent is never touched (that protects the hand-made links and keeps
the "at most one parent per child" invariant), and a link that would close
a loop is refused.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Counter as CounterType, Dict, Iterable, List
from typing import Optional, Set, Tuple

ZWS = "\u200B"

# Hiragana plus the katakana prolonged sound mark.  Sudachi's get_lemma()
# returns None for these, so they can never be linked; they are counted
# separately because they are a large and explainable part of the total
# (1414 terms, 17.4%, see the task doc).
HIRAGANA_ONLY = re.compile(r"^[\u3041-\u3093\u30FC]+$")

# Why a term/candidate was left alone.  First match wins, and the order
# matters -- see select_links().
REASON_HAS_PARENT = "already_has_parent"
REASON_MULTI_TOKEN = "multi_token"
REASON_KANA = "kana_only"
REASON_NO_LEMMA = "no_lemma"
REASON_CONCATENATED = "concatenated_lemma"
REASON_SYNC_FLAG = "sync_flag_set"
REASON_SELF = "self_link"
REASON_CYCLE = "cycle"

# How many examples to keep per skip reason, for the report.
SAMPLE_CAP = 8

# resolve_lemma_root() follows at most this many links.
MAX_LEMMA_HOPS = 3


@dataclass(frozen=True)
class Link:
    "One child -> parent link that should exist."

    child_id: int
    child_text: str
    child_status: int
    lemma: str


@dataclass
class Plan:
    "The result of scanning the terms of one language."

    total_terms: int = 0
    links: List[Link] = field(default_factory=list)
    skipped: CounterType = field(default_factory=Counter)
    skipped_samples: Dict[str, List[str]] = field(default_factory=dict)
    chained_lemma_count: int = 0
    chained_samples: List[str] = field(default_factory=list)

    def skipped_total(self):
        "Number of terms that were not turned into links."
        return sum(self.skipped.values())


def resolve_lemma_root(
    lemma_of: Callable[[str], Optional[str]], text: str
) -> Tuple[Optional[str], List[str]]:
    """
    The dictionary form of text, following the lemma chain, plus the chain.

    Sudachi's dictionary form is usually already a root (食べた -> 食べる,
    and 食べる has no further lemma), but a single kanji can point at an
    inflected entry: 在 -> 在り -> 在る.  Using the root avoids creating
    the intermediate term (在り) that nobody asked for, and it is what
    makes a second run of the backfill a no-op.
    """
    chain = []
    seen = {text}
    lemma = lemma_of(text)
    while lemma and lemma not in seen and len(chain) <= MAX_LEMMA_HOPS:
        chain.append(lemma)
        seen.add(lemma)
        nxt = lemma_of(lemma)
        if not nxt or nxt == lemma:
            break
        lemma = nxt
    return (chain[-1] if chain else None), chain


# How to treat terms whose text carries zero-width spaces (i.e. terms
# made of several tokens).  The task doc's hard constraint says "only
# single-token terms", and "skip" is that rule literally.  But the most
# useful links in this database are of the other shape -- 入りました ->
# 入る is three tokens and *one* content word -- so the relaxed policy is
# available for comparison.  Nonsense shapes (似ている -> 似るいる, or the
# Chinese sentences that were imported as Japanese terms) are rejected by
# the content-word rule either way.
POLICY_SKIP_MULTI_TOKEN = "skip"
POLICY_SINGLE_CONTENT_WORD = "single-content-word"
MULTI_TOKEN_POLICIES = (POLICY_SKIP_MULTI_TOKEN, POLICY_SINGLE_CONTENT_WORD)


def select_links(  # pylint: disable=too-many-arguments
    rows: Iterable[Tuple[int, str, int]],
    lemma_of: Callable[[str], Optional[str]],
    content_token_count_of: Callable[[str], int],
    ids_with_parents: Set[int],
    multi_token_policy: str = POLICY_SKIP_MULTI_TOKEN,
) -> Plan:
    """
    Build the plan of links to create.

    rows: (term id, text, status) triples, in any order.
    lemma_of: the language parser's get_lemma(); None or the text itself
        means "no dictionary form".
    content_token_count_of: number of content words in the text.  A term
        that is a concatenation of several content words (似ている) has a
        lemma that is a concatenation too (似るいる), and that is not a
        term anybody wants in their vocabulary list.
    ids_with_parents: ids of terms that already have at least one parent.
    multi_token_policy: see POLICY_* above.
    """
    plan = Plan()
    for term_id, text, status in rows:
        plan.total_terms += 1
        text = text or ""

        # Safety rails first: never touch existing links.
        if term_id in ids_with_parents:
            _skip(plan, REASON_HAS_PARENT, text)
            continue

        content_words = None
        if ZWS in text:
            if multi_token_policy == POLICY_SKIP_MULTI_TOKEN:
                _skip(plan, REASON_MULTI_TOKEN, text)
                continue
            content_words = content_token_count_of(text)
            if content_words != 1:
                _skip(plan, REASON_MULTI_TOKEN, text)
                continue

        if HIRAGANA_ONLY.match(text.replace(ZWS, "")):
            _skip(plan, REASON_KANA, text)
            continue

        lemma, chain = resolve_lemma_root(lemma_of, text)
        # get_lemma() strips the zero-width spaces, so the comparison has
        # to strip them too -- that is what find_or_new() does.
        if not lemma or lemma == text.replace(ZWS, ""):
            _skip(plan, REASON_NO_LEMMA, text)
            continue

        if content_words is None:
            content_words = content_token_count_of(text)
        # The term itself has to be one word; the parent has to be
        # something other than a concatenation (> 1, so that the root of
        # a kana-only lemma isn't rejected for counting 0).
        if content_words != 1 or content_token_count_of(lemma) > 1:
            _skip(plan, REASON_CONCATENATED, text, lemma)
            continue

        if len(chain) > 1:
            plan.chained_lemma_count += 1
            if len(plan.chained_samples) < SAMPLE_CAP:
                plan.chained_samples.append(" -> ".join([text, *chain]))

        plan.links.append(Link(term_id, text, status, lemma))

    return plan


def would_cycle(
    child_id: int,
    parent_id: int,
    parent_of: Callable[[int], Optional[int]],
    max_depth: int = 10,
) -> bool:
    """
    True if adding child_id -> parent_id would close a loop.

    parent_of(term_id) returns the term's (single) parent id, or None.
    A chain deeper than max_depth is treated as a loop: lemma chains are
    one or two links long, so anything longer is not something to guess
    about.
    """
    seen = {child_id}
    node = parent_id
    for _ in range(max_depth):
        if node is None:
            return False
        if node in seen:
            return True
        seen.add(node)
        node = parent_of(node)
    return True


def _skip(plan: Plan, reason: str, text: str, lemma: Optional[str] = None):
    "Count a skipped term, keeping a few examples for the report."
    plan.skipped[reason] += 1
    samples = plan.skipped_samples.setdefault(reason, [])
    if len(samples) < SAMPLE_CAP:
        samples.append(text if lemma is None else f"{text} -> {lemma}")
