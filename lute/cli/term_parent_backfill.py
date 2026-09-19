"""
Backfill lemma (dictionary form) parents for the terms of a language.

Roadmap 2.3.  The spec, the measured numbers and the hard constraints are
in docs/term-parent-lemma-task.md -- read that before running this on
anything you care about.

Dry run by default::

    flask --app lute.app_factory cli parent_backfill --language Japanese
    flask --app lute.app_factory cli parent_backfill --language Japanese --limit 50 --commit
    flask --app lute.app_factory cli parent_backfill_undo --audit /tmp/parents.jsonl

What it writes: rows in ``wordparents``, plus (only when needed) new rows
in ``words`` for the parent terms.  What it never writes: ``WoSyncStatus``
stays 0 on every new link, and no existing term's ``WoStatus`` is
changed.

The "no status changes" part is not a promise, it is a property of the
schema: ``trig_wordparents_after_insert_update_parent_WoStatus_if_following``
only copies a child's status onto its parent when *the child* has
WoSyncStatus = 1, and a child that has no parent yet has never had sync
turned on.  The one status write left is the status of the parent terms
this creates, and that is a new row, not a change to an existing one.

Note this is deliberately *not* routed through TermRepository.add() /
_build_db_term(): that path rewrites the child's status, and its
_find_or_create_parent() assigns the child's status to an *existing*
parent that is status 0, which is exactly the "never change an existing
WoStatus" constraint this tool has to respect.
"""

# %-formatting is used throughout rather than f-strings: this command runs
# on the production server (python 3.11), where an f-string cannot contain
# a backslash or reuse its own quote character inside the expression.
# pylint: disable=consider-using-f-string

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, insert, select, text

from lute.db import db
from lute.models.repositories import LanguageRepository
from lute.models.term import Status, Term, wordparents
from lute.term.lemma_parents import (
    MULTI_TOKEN_POLICIES,
    POLICY_SINGLE_CONTENT_WORD,
    POLICY_SKIP_MULTI_TOKEN,
    REASON_CYCLE,
    REASON_KANA,
    REASON_CONCATENATED,
    REASON_HAS_PARENT,
    REASON_MULTI_TOKEN,
    REASON_NO_LEMMA,
    REASON_SELF,
    REASON_SYNC_FLAG,
    ZWS,
    Link,
    select_links,
    would_cycle,
)


class TermParentBackfillError(Exception):
    "Something about this database/language makes the backfill unsafe."


REASON_LABELS = {
    REASON_HAS_PARENT: "already has a parent (left alone)",
    REASON_MULTI_TOKEN: "multi-token term (contains a zero-width space)",
    REASON_KANA: "all hiragana: no dictionary form",
    REASON_NO_LEMMA: "parser returned no dictionary form",
    REASON_CONCATENATED: "lemma is a concatenation of several words",
    REASON_SYNC_FLAG: "child has WoSyncStatus = 1",
    REASON_SELF: "lemma is the term itself",
    REASON_CYCLE: "link would close a loop",
}


@dataclass
class BackfillResult:  # pylint: disable=too-many-instance-attributes
    "Everything the report needs."

    language: str = ""
    language_id: int = 0
    total_terms: int = 0
    terms_with_parent_before: int = 0
    limit: Optional[int] = None
    links: List[Tuple[Link, Optional[int], bool]] = field(default_factory=list)
    parents_new: int = 0
    parents_reused: int = 0
    new_parent_statuses: Counter = field(default_factory=Counter)
    reused_parent_statuses: Counter = field(default_factory=Counter)
    links_written: int = 0
    skipped: Counter = field(default_factory=Counter)
    skipped_samples: Dict[str, List[str]] = field(default_factory=dict)
    child_statuses: Counter = field(default_factory=Counter)
    chained_lemma_count: int = 0
    chained_samples: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    committed: bool = False
    audit_path: Optional[str] = None
    # Links that the relaxed multi-token policy would add (see
    # select_links); filled in when the strict policy is used.
    relaxed_link_count: int = 0
    relaxed_parent_count: int = 0

    def skipped_total(self):
        "Number of terms that were not turned into links."
        return sum(self.skipped.values())


def run_backfill(  # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements,too-many-positional-arguments
    language_name: str,
    commit: bool = False,
    limit: Optional[int] = None,
    new_parent_status: str = "inherit",
    audit_path: Optional[str] = None,
    multi_token_policy: str = POLICY_SKIP_MULTI_TOKEN,
) -> BackfillResult:
    """
    Scan a language's terms, then (when commit) create the links.

    new_parent_status is "inherit" (the parent is created with the status
    of the first child that points at it, matching what the manual paths
    do) or "zero" (created as unknown).
    """
    if new_parent_status not in ("inherit", "zero"):
        raise TermParentBackfillError(
            f"new_parent_status must be 'inherit' or 'zero', was '{new_parent_status}'."
        )
    if multi_token_policy not in MULTI_TOKEN_POLICIES:
        raise TermParentBackfillError(
            f"multi_token_policy must be one of {MULTI_TOKEN_POLICIES}, "
            f"was '{multi_token_policy}'."
        )

    lang = LanguageRepository(db.session).find_by_name(language_name)
    if lang is None:
        raise TermParentBackfillError(f"No language named '{language_name}'.")

    parser = lang.parser
    content_token_count = getattr(parser, "content_token_count", None)
    if content_token_count is None or not callable(content_token_count):
        raise TermParentBackfillError(
            f"Parser type '{lang.parser_type}' has no content_token_count(), so the "
            "'lemma is a concatenation' filter cannot run.  Nothing was written."
        )
    if parser.get_lemma("日本語") is None and parser.get_lemma("食べた") is None:
        raise TermParentBackfillError(
            f"Parser type '{lang.parser_type}' returns no lemmas; nothing to backfill."
        )

    res = BackfillResult(language=lang.name, language_id=lang.id, limit=limit)

    words = Term.__table__
    rows = db.session.execute(
        select(words.c.WoID, words.c.WoText, words.c.WoStatus).where(
            words.c.WoLgID == lang.id
        )
    ).all()
    status_by_id = {r[0]: r[2] for r in rows}
    ids_with_parents = {
        r[0] for r in db.session.execute(select(wordparents.c.WpWoID).distinct())
    }
    res.terms_with_parent_before = len(
        [i for i in ids_with_parents if i in status_by_id]
    )

    # get_lemma() and the content-word count both tokenize, and the
    # alternative-policy pass below would otherwise double the work.
    lemma_cache: Dict[str, Optional[str]] = {}
    count_cache: Dict[str, int] = {}

    def lemma_of(s):
        if s not in lemma_cache:
            lemma_cache[s] = parser.get_lemma(s)
        return lemma_cache[s]

    def content_words_of(s):
        if s not in count_cache:
            count_cache[s] = content_token_count(s)
        return count_cache[s]

    plan = select_links(
        rows, lemma_of, content_words_of, ids_with_parents, multi_token_policy
    )
    res.total_terms = plan.total_terms
    res.skipped = plan.skipped
    res.skipped_samples = plan.skipped_samples
    res.chained_lemma_count = plan.chained_lemma_count
    res.chained_samples = plan.chained_samples

    if multi_token_policy == POLICY_SKIP_MULTI_TOKEN:
        # The task doc's constraint says "single-token terms only", which
        # excludes the shape most of the existing hand-made links have
        # (入りました -> 入る is three tokens and one content word).  Report
        # the other number so that decision is made on data -- counted the
        # same way as the links above, i.e. without the self-links.
        relaxed = select_links(
            rows,
            lemma_of,
            content_words_of,
            ids_with_parents,
            POLICY_SINGLE_CONTENT_WORD,
        )
        relaxed = [lnk for lnk in relaxed.links if not _is_self_link(lang, lnk)]
        res.relaxed_link_count = len(relaxed)
        res.relaxed_parent_count = len(
            {lang.get_lowercase(lnk.lemma) for lnk in relaxed}
        )

    links = sorted(plan.links, key=lambda lnk: lnk.child_id)
    if limit is not None:
        links = links[:limit]

    # Resolve the parent of each link.  One pass, because the report wants
    # distinct-parent counts and the commit pass needs the same decisions.
    term_id_by_lc = {
        r[0]: r[1]
        for r in db.session.execute(
            select(words.c.WoTextLC, words.c.WoID).where(words.c.WoLgID == lang.id)
        )
    }
    seen_lc: Dict[str, Tuple[Optional[int], bool]] = {}
    resolutions: List[Tuple[Link, str, bool]] = []
    for lnk in links:
        if _is_self_link(lang, lnk):
            _skip(res, REASON_SELF, lnk)
            continue
        lc = lang.get_lowercase(lnk.lemma)
        if lc not in seen_lc:
            pid = term_id_by_lc.get(lc)
            is_new = pid is None
            seen_lc[lc] = (pid, is_new)
            if is_new:
                res.parents_new += 1
                status = (
                    lnk.child_status
                    if new_parent_status == "inherit"
                    else Status.UNKNOWN
                )
                res.new_parent_statuses[status] += 1
            else:
                res.parents_reused += 1
                res.reused_parent_statuses[status_by_id.get(pid, 0)] += 1
        _pid, is_new = seen_lc[lc]
        resolutions.append((lnk, lc, is_new))

    res.child_statuses = Counter(lnk.child_status for lnk, _lc, _n in resolutions)

    if commit:
        _write(
            res,
            lang,
            resolutions,
            seen_lc,
            term_id_by_lc,
            audit_path,
            new_parent_status,
        )
    else:
        res.links = [(lnk, seen_lc[lc][0], is_new) for lnk, lc, is_new in resolutions]

    return res


def _write(  # pylint: disable=too-many-arguments,too-many-locals,too-many-positional-arguments
    res, lang, resolutions, seen_lc, term_id_by_lc, audit_path, new_parent_status
):
    "Create the parent terms and the links."
    words = Term.__table__
    parent_of = {
        r[0]: r[1]
        for r in db.session.execute(
            select(wordparents.c.WpWoID, wordparents.c.WpParentWoID)
        )
    }
    sync_children = {
        r[0]
        for r in db.session.execute(
            select(words.c.WoID).where(words.c.WoSyncStatus == 1)
        )
    }

    # 1. Create the missing parent terms, one per distinct lemma.
    for lc, (pid, is_new) in seen_lc.items():
        if not is_new:
            continue
        lnk = next(link for link, link_lc, _n in resolutions if link_lc == lc)
        parent = Term.create_term_no_parsing(lang, lnk.lemma)
        if ZWS in parent.text:  # pragma: no cover - defensive
            res.warnings.append(
                f"refused to create parent '{_vis(parent.text)}' (it re-tokenized)"
            )
            continue
        parent.status = (
            lnk.child_status if new_parent_status == "inherit" else Status.UNKNOWN
        )
        parent.sync_status = False
        db.session.add(parent)
        db.session.flush()
        seen_lc[lc] = (parent.id, True)
        term_id_by_lc[lc] = parent.id
        parent_of[parent.id] = None

    # 2. Add the links.
    records = []
    for lnk, lc, _is_new in resolutions:
        pid, is_new = seen_lc[lc]
        if pid is None:
            res.warnings.append(f"no parent term for '{lnk.lemma}', link skipped")
            continue
        if lnk.child_id in sync_children:
            _skip(res, REASON_SYNC_FLAG, lnk)
            continue
        if would_cycle(lnk.child_id, pid, parent_of.get):
            _skip(res, REASON_CYCLE, lnk)
            continue
        db.session.execute(
            insert(wordparents)
            .values(WpWoID=lnk.child_id, WpParentWoID=pid)
            .prefix_with("OR IGNORE")
        )
        parent_of[lnk.child_id] = pid
        res.links_written += 1
        res.links.append((lnk, pid, is_new))
        records.append(
            {
                "child_id": lnk.child_id,
                "child_text": lnk.child_text,
                "child_status": lnk.child_status,
                "parent_id": pid,
                "parent_text": lnk.lemma,
                "parent_created": is_new,
            }
        )

    db.session.commit()

    if audit_path:
        with open(audit_path, "a", encoding="utf-8") as f:
            for rec in records:
                rec["written_at"] = datetime.utcnow().isoformat()
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        res.audit_path = audit_path
    res.committed = True


def undo_backfill(audit_path: str, commit: bool = False, delete_created_terms=False):
    """
    Undo what a previous --commit did, using its audit log.

    The links are always removed.  The parent terms that the run created
    are only removed with delete_created_terms, and only when nothing else
    points at them: a term that has gained other links, tags, images or a
    user-written translation is left in place (it is vocabulary data by
    then, not backfill residue).

    Returns (links_removed, terms_removed, terms_kept).
    """
    with open(audit_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    pairs = sorted({(r["child_id"], r["parent_id"]) for r in records})
    created = sorted({r["parent_id"] for r in records if r.get("parent_created")})

    if not commit:
        return (len(pairs), 0, len(created))

    removed = 0
    for child_id, parent_id in pairs:
        result = db.session.execute(
            delete(wordparents).where(
                wordparents.c.WpWoID == child_id,
                wordparents.c.WpParentWoID == parent_id,
            )
        )
        removed += result.rowcount or 0

    terms_removed = 0
    terms_kept = 0
    if delete_created_terms:
        for term_id in created:
            if _term_is_referenced(term_id):
                terms_kept += 1
                continue
            db.session.execute(
                delete(Term.__table__).where(Term.__table__.c.WoID == term_id)
            )
            terms_removed += 1

    db.session.commit()
    return (removed, terms_removed, terms_kept)


def _term_is_referenced(term_id):
    """
    True if a backfill-created term is no longer safe to delete.

    Terms live on their own: there is no word/text join table, so the
    only things to check are other parent links and data a user might
    have added.
    """
    checks = [
        (
            "select count(*) from wordparents where WpWoID = :t or WpParentWoID = :t",
            "other parent links",
        ),
        ("select count(*) from wordtags where WtWoID = :t", "term tags"),
        ("select count(*) from wordimages where WiWoID = :t", "images"),
        ("select count(*) from wordflashmessages where WfWoID = :t", "flash message"),
        (
            "select count(*) from words"
            " where WoID = :t and (WoTranslation is not null and WoTranslation != '')",
            "a translation",
        ),
    ]
    for sql, label in checks:
        n = db.session.execute(text(sql).bindparams(t=term_id)).scalar()
        if n:
            # pylint: disable=consider-using-f-string
            print("  keeping term %d: it now has %s" % (term_id, label))
            return True
    return False


def format_report(res: BackfillResult) -> str:
    "Human readable report."
    total = max(res.total_terms, 1)

    def pct(n):
        "%5.1f%% of the terms."
        return "%5.1f%%" % (n * 100.0 / total)

    with_parent = res.terms_with_parent_before + (
        res.links_written if res.committed else len(res.links)
    )
    lines = [
        "Language            : %s (id %d)" % (res.language, res.language_id),
        "Terms               : %d" % res.total_terms,
        "Have a parent today : %d (%s)"
        % (res.terms_with_parent_before, pct(res.terms_with_parent_before)),
        "Links               : %d (%s)  -> coverage %s"
        % (len(res.links), pct(len(res.links)), pct(with_parent)),
    ]
    if res.limit is not None:
        lines.append("  (limited to %d link(s): canary run)" % res.limit)
    if res.relaxed_link_count:
        lines.append(
            "  with --multi-token-policy %s: %d links, %d parent terms"
            % (
                POLICY_SINGLE_CONTENT_WORD,
                res.relaxed_link_count,
                res.relaxed_parent_count,
            )
        )
    lines.append(
        "Parent terms        : %d new, %d already existed"
        % (res.parents_new, res.parents_reused)
    )
    if res.parents_new:
        lines.append(
            "  statuses of the new parent terms: %s" % _hist(res.new_parent_statuses)
        )
    if res.parents_reused:
        lines.append(
            "  current statuses of the reused parents: %s"
            % _hist(res.reused_parent_statuses)
        )
    if res.child_statuses:
        lines.append(
            "  statuses of the children that get a parent: %s"
            % _hist(res.child_statuses)
        )
    if res.chained_lemma_count:
        lines.append(
            "  links that followed a lemma chain (%d): %s"
            % (res.chained_lemma_count, ", ".join(res.chained_samples))
        )
    if res.committed:
        lines.append("Links written       : %d" % res.links_written)
        lines.append("Sync statuses set   : 0 (never set by this tool)")
        if res.audit_path:
            lines.append("Audit log           : %s" % res.audit_path)
    lines.append("Not linked          : %d" % res.skipped_total())
    for reason, count in res.skipped.most_common():
        lines.append(
            "  %-46s %6d   e.g. %s"
            % (
                REASON_LABELS.get(reason, reason),
                count,
                ", ".join(res.skipped_samples.get(reason, [])[:3]),
            )
        )
    for warning in res.warnings:
        lines.append("WARNING: %s" % warning)
    return "\n".join(lines)


def _hist(counts):
    "Render a status histogram."
    return ", ".join("%s:%d" % (k, v) for k, v in sorted(counts.items()))


def _skip(res, reason, lnk):
    "Count a link that was refused at resolution time."
    res.skipped[reason] += 1
    samples = res.skipped_samples.setdefault(reason, [])
    if len(samples) < 8:
        samples.append("%s -> %s" % (lnk.child_text, lnk.lemma))


def _vis(s):
    "Make zero-width spaces visible."
    return (s or "").replace(ZWS, "\u00B7")


def _is_self_link(lang, lnk):
    "True when the lemma is the term itself (the db keys on lowercase text)."
    return lang.get_lowercase(lnk.lemma) == lang.get_lowercase(
        lnk.child_text.replace(ZWS, "")
    )


def plan_as_dict(res: BackfillResult) -> dict:
    "The whole plan as JSON, for diffing two runs."
    return {
        "language": res.language,
        "language_id": res.language_id,
        "total_terms": res.total_terms,
        "terms_with_parent_before": res.terms_with_parent_before,
        "limit": res.limit,
        "committed": res.committed,
        "parents_new": res.parents_new,
        "parents_reused": res.parents_reused,
        "new_parent_statuses": dict(res.new_parent_statuses),
        "reused_parent_statuses": dict(res.reused_parent_statuses),
        "child_statuses": dict(res.child_statuses),
        "chained_lemma_count": res.chained_lemma_count,
        "links_written": res.links_written,
        "skipped": dict(res.skipped),
        "relaxed_link_count": res.relaxed_link_count,
        "relaxed_parent_count": res.relaxed_parent_count,
        "links": [
            {
                "child_id": lnk.child_id,
                "child_text": lnk.child_text,
                "child_status": lnk.child_status,
                "parent_id": pid,
                "parent_text": lnk.lemma,
                "parent_created": is_new,
            }
            for lnk, pid, is_new in res.links
        ],
    }
