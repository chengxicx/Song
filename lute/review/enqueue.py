"""
Enqueue review cards from active review specs.

A sync is purely additive: cards already in the queue are never
touched or deleted, so changing or deactivating a spec cannot
destroy scheduling history.  Cloze cards require the term to have
at least one read sentence (the card blanks the term inside it).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import insert
from sqlalchemy.orm import selectinload

from lute.ankiexport.criteria import evaluate_criteria
from lute.db import db
from lute.models.review import ReviewCard, ReviewSpec
from lute.models.term import Term as DBTerm
from lute.term.model import ReferencesRepository

# Learning-range statuses only: unknown (0), ignored (98) and
# well-known (99) terms are not review material.
_LEARNING_STATUSES = [1, 2, 3, 4, 5]


@dataclass
class SpecSyncReport:
    "Outcome for one spec."
    spec_name: str
    terms_matched: int = 0
    cards_added: int = 0
    cards_by_type: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    "Outcome of a run_sync."
    spec_reports: list = field(default_factory=list)
    cards_added: int = 0
    skipped_no_sentence: int = 0
    errors: dict = field(default_factory=dict)
    committed: bool = False

    def as_dict(self):
        "JSON shape for the sync endpoint."
        return {
            "cards_added": self.cards_added,
            "skipped_no_sentence": self.skipped_no_sentence,
            "errors": self.errors,
            "committed": self.committed,
            "specs": [
                {
                    "name": r.spec_name,
                    "terms_matched": r.terms_matched,
                    "cards_added": r.cards_added,
                    "cards_by_type": r.cards_by_type,
                }
                for r in self.spec_reports
            ],
        }


def format_report(result):
    "Human-readable report for CLI / flash messages."
    lines = []
    if not result.committed:
        lines.append("DRY RUN - nothing written.  Pass --commit to write.")
    for r in result.spec_reports:
        by_type = ", ".join(f"{k}: {v}" for k, v in r.cards_by_type.items())
        lines.append(
            f"{r.spec_name}: matched {r.terms_matched} terms, "
            f"added {r.cards_added} cards ({by_type or 'no new cards'})"
        )
    if result.skipped_no_sentence > 0:
        lines.append(
            f"Skipped {result.skipped_no_sentence} cloze cards "
            "(term has no read sentence yet)."
        )
    for name, err in result.errors.items():
        lines.append(f"ERROR in spec {name}: {err}")
    lines.append(f"Total cards added: {result.cards_added}")
    return "\n".join(lines)


@dataclass
class _SyncContext:
    "Shared state threaded through one sync run."
    now: object
    existing: set
    refsrepo: object
    sentences_checked: dict
    result: SyncResult


def run_sync(commit=False, limit=None):
    """
    Evaluate all active specs and enqueue matching terms' cards.

    Dry-run by default: with commit=False nothing is written.
    limit caps the number of cards written (canary runs).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = SyncResult(committed=commit)

    specs = db.session.query(ReviewSpec).filter(ReviewSpec.active.is_(True)).all()
    if not specs:
        return result

    terms = _load_candidates()
    if not terms:
        result.spec_reports = [SpecSyncReport(s.name) for s in specs]
        return result

    ctx = _SyncContext(
        now=now,
        existing=_existing_pairs(),
        refsrepo=ReferencesRepository(db.session, limit=1),
        sentences_checked={},
        result=result,
    )
    rows = []

    for spec in specs:
        report, spec_rows = _spec_cards(spec, terms, ctx)
        result.spec_reports.append(report)
        rows.extend(spec_rows)

    if limit is not None:
        rows = rows[:limit]
    result.cards_added = len(rows)

    if rows and commit:
        db.session.execute(insert(ReviewCard).prefix_with("OR IGNORE"), rows)
        db.session.commit()
    elif not commit:
        db.session.rollback()

    return result


def _spec_cards(spec, terms, ctx):
    """
    Collect the new card rows for one spec, updating ctx.existing (the
    set of queued pairs), the no-sentence skip counter, and the report.
    """
    report = SpecSyncReport(spec_name=spec.name)
    rows = []
    result = ctx.result
    try:
        for term in terms:
            if not evaluate_criteria(spec.criteria, term):
                continue
            report.terms_matched += 1
            for card_type in spec.card_types_enabled:
                if (term.id, card_type) in ctx.existing:
                    continue
                if card_type == "cloze" and not _has_sentence(
                    term.id, ctx.refsrepo, ctx.sentences_checked
                ):
                    result.skipped_no_sentence += 1
                    continue
                ctx.existing.add((term.id, card_type))
                report.cards_added += 1
                report.cards_by_type[card_type] = (
                    report.cards_by_type.get(card_type, 0) + 1
                )
                rows.append(
                    {
                        "term_id": term.id,
                        "card_type": card_type,
                        "due": ctx.now,
                        "state": ReviewCard.STATE_NEW,
                        "reps": 0,
                        "lapses": 0,
                        "created": ctx.now,
                        "spec_id": spec.id,
                    }
                )
    except Exception as ex:  # pylint: disable=broad-exception-caught
        # A broken spec is reported, not fatal: keep syncing the rest.
        result.errors[spec.name] = str(ex)
    return report, rows


def _load_candidates():
    "Terms in the learning statuses, with tags/parents/language eager-loaded."
    return (
        db.session.query(DBTerm)
        .filter(DBTerm.status.in_(_LEARNING_STATUSES))
        .options(
            selectinload(DBTerm.language),
            selectinload(DBTerm.term_tags),
            selectinload(DBTerm.parents).selectinload(DBTerm.term_tags),
            selectinload(DBTerm.images),
        )
        .all()
    )


def _existing_pairs():
    "(term_id, card_type) pairs already queued."
    return set(db.session.query(ReviewCard.term_id, ReviewCard.card_type).all())


def _has_sentence(term_id, refsrepo, cache):
    "True if the term has at least one read sentence."
    if term_id not in cache:
        refs = refsrepo.find_references_by_id(term_id)
        cache[term_id] = len(refs.get("term") or []) > 0
    return cache[term_id]
