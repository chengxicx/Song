"""
Two-way reconciliation between Lute terms and an Eudic studylist.

## Why this is a reconciliation and not an incremental sync

Lute's ``words`` table has no usable watermark: ``WoCreated`` is reset by
a trigger when a term leaves Unknown status, and ``WoStatusChanged`` only
moves when the status changes.  So instead of "what changed since last
time", every run computes the *desired* state of both sides and three-way
compares against the baseline stored in ``eudicsync``:

    local_changed  = current status      != last_synced_status
    remote_changed = current remote star != last_synced_star

That distinguishes "I changed it here" from "I changed it there", which a
timestamp comparison could not.

## Conflict rule

Eudic exposes no modified time at all (only ``add_time``, the creation
time), so a genuine simultaneous edit cannot be ordered.  Lute wins: it
is the place where reading and review actually happen.

## What cannot be synced

* Translations and example sentences never leave Lute.  The bulk write
  endpoint only accepts ``word`` and ``star``.
* Well Known (99) cannot become Eudic's "mastered".  That list is
  read/delete only, so 99 degrades to star 5.
* Words at status 0 (Unknown) or 98 (Ignored) are never pushed, and a
  pull never overwrites them -- "ignored here" must not mean "deleted
  there".
"""

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from lute.ai.crypto import decrypt, encrypt
from lute.eudic.client import EudicClient, EudicError, EudicNotConfigured, RemoteWord
from lute.eudic.mapping import (
    LOCKED_STATUSES,
    PUSHABLE_STATUSES,
    clean_exp,
    clean_phon,
    push_form,
    split_reading,
    star_for_status,
    status_for_star,
    truncate,
    word_key,
)
from lute.eudic.models import EudicConfigRepository, EudicSyncRepository
from lute.models.repositories import TermRepository as DBTermRepository
from lute.term.model import Repository as TermRepository

PUSH_BATCH_SIZE = 500
MAX_REPORTED_FAILURES = 100


@dataclass
class SyncScope:
    "One (Lute language -> Eudic bucket + studylist) mapping."

    lg_id: int
    language_name: str
    bucket: str
    category_id: str
    category_name: str = ""


@dataclass
class PushItem:
    "A term to send (or to record as already in sync)."

    term_id: int
    word: str
    key: str
    star: int
    local_status: int
    conflict: bool = False


@dataclass
class PullItem:
    "A remote word to bring into Lute."

    remote: RemoteWord
    key: str
    term_id: Optional[int] = None
    local_status: Optional[int] = None


@dataclass
class SyncPlan:
    "What a run would do, before anything is written."

    push_new: List[PushItem] = field(default_factory=list)
    push_update: List[PushItem] = field(default_factory=list)
    conflicts: List[PushItem] = field(default_factory=list)
    adopt: List[PushItem] = field(default_factory=list)
    pull_new: List[PullItem] = field(default_factory=list)
    pull_update: List[PullItem] = field(default_factory=list)
    locked: int = 0
    skipped: int = 0
    unchanged: int = 0
    remote_total: int = 0
    remote_pages: int = 0
    remote_truncated: bool = False
    duplicates: int = 0

    def counts(self):
        "The numbers the page shows."
        return {
            "push_new": len(self.push_new),
            "push_update": len(self.push_update),
            "conflicts": len(self.conflicts),
            "adopt": len(self.adopt),
            "pull_new": len(self.pull_new),
            "pull_update": len(self.pull_update),
            "locked": self.locked,
            "skipped": self.skipped,
            "unchanged": self.unchanged,
            "remote_total": self.remote_total,
            "remote_pages": self.remote_pages,
            "remote_truncated": self.remote_truncated,
            "duplicate_keys": self.duplicates,
        }


@dataclass
class SyncResult:
    "What a run actually did."

    mode: str
    plan: Dict = field(default_factory=dict)
    pushed_new: int = 0
    pushed_updated: int = 0
    pushed_conflicts: int = 0
    adopted: int = 0
    pulled_new: int = 0
    pulled_updated: int = 0
    locked: int = 0
    skipped: int = 0
    failure_count: int = 0
    failures: List[Dict] = field(default_factory=list)
    remote_total: int = 0
    remote_pages: int = 0
    remote_truncated: bool = False
    duplicate_keys: int = 0
    elapsed_seconds: float = 0.0
    aborted_reason: str = ""
    message: str = ""

    def to_dict(self):
        "JSON payload for the page."
        return {
            "mode": self.mode,
            "plan": self.plan,
            "pushed_new": self.pushed_new,
            "pushed_updated": self.pushed_updated,
            "pushed_conflicts": self.pushed_conflicts,
            "adopted": self.adopted,
            "pulled_new": self.pulled_new,
            "pulled_updated": self.pulled_updated,
            "locked": self.locked,
            "skipped": self.skipped,
            "failure_count": self.failure_count,
            "failures": self.failures,
            "remote_total": self.remote_total,
            "remote_pages": self.remote_pages,
            "remote_truncated": self.remote_truncated,
            "duplicate_keys": self.duplicate_keys,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "aborted_reason": self.aborted_reason,
            "message": self.message,
        }


class EudicService:
    "Sync operations for the current user's database."

    def __init__(self, session, client=None):
        self.session = session
        self._client = client
        self.config_repo = EudicConfigRepository(session)
        self.sync_repo = EudicSyncRepository(session)
        self.term_repo = DBTermRepository(session)

    # -- configuration ----------------------------------------------------

    @property
    def client(self):
        "A client built from the stored key, created on first use."
        if self._client is None:
            key = self.api_key()
            if not key:
                raise EudicNotConfigured()
            self._client = EudicClient(key)
        return self._client

    def api_key(self):
        "The decrypted key, or '' when not configured."
        row = self.config_repo.load()
        return decrypt(row.api_key or "")

    def api_key_mask(self):
        "A hint for the form placeholder; never the whole key."
        key = self.api_key()
        if not key:
            return ""
        return "••••••••" + key[-4:]

    def set_api_key(self, raw):
        """
        Store a key.  Blank means "keep the one already saved", so saving
        the rest of the form never wipes the credential.
        """
        text = (raw or "").strip()
        if not text:
            return
        row = self.config_repo.load()
        row.api_key = encrypt(text)
        self.config_repo.save(row)
        self._client = None

    def clear_api_key(self):
        "Forget the key; the only way to disconnect."
        row = self.config_repo.load()
        row.api_key = ""
        self.config_repo.save(row)
        self._client = None

    def scopes(self):
        "``{lg_id: {bucket, category_id, category_name}}``."
        row = self.config_repo.load()
        try:
            raw = json.loads(row.scopes or "{}")
        except ValueError:
            return {}
        if not isinstance(raw, dict):
            return {}
        out = {}
        for k, v in raw.items():
            try:
                lg_id = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict) and v.get("bucket"):
                out[lg_id] = {
                    "bucket": str(v.get("bucket")),
                    "category_id": str(v.get("category_id") or "0"),
                    "category_name": str(v.get("category_name") or ""),
                }
        return out

    def set_scope(self, lg_id, bucket, category_id, category_name=""):
        "Remember which Eudic studylist a Lute language maps to."
        row = self.config_repo.load()
        scopes = self.scopes()
        scopes[int(lg_id)] = {
            "bucket": bucket,
            "category_id": str(category_id),
            "category_name": category_name or "",
        }
        row.scopes = json.dumps({str(k): v for k, v in scopes.items()})
        self.config_repo.save(row)

    def remove_scope(self, lg_id):
        "Stop syncing a language (the mapping rows are left alone)."
        row = self.config_repo.load()
        scopes = self.scopes()
        scopes.pop(int(lg_id), None)
        row.scopes = json.dumps({str(k): v for k, v in scopes.items()})
        self.config_repo.save(row)

    def scope_for(self, lg_id):
        "The configured scope, or None."
        raw = self.scopes().get(int(lg_id))
        if raw is None:
            return None
        return SyncScope(
            lg_id=int(lg_id),
            language_name="",
            bucket=raw["bucket"],
            category_id=raw["category_id"],
            category_name=raw.get("category_name") or "",
        )

    def list_categories(self, bucket):
        "Live list of studylists, for the form's dropdown."
        return self.client.list_categories(bucket)

    def stats(self, scope):
        "Counters for the page header."
        return self.sync_repo.stats(scope.lg_id, scope.bucket, scope.category_id)

    def recent_errors(self, scope, limit=50):
        "The most recent failures."
        return self.sync_repo.recent_errors(
            scope.lg_id, scope.bucket, scope.category_id, limit
        )

    # -- reconciliation ---------------------------------------------------

    def fetch_snapshot(self, scope, progress=None):
        "Read the whole remote studylist."

        def on_page(page, count):
            if progress is not None:
                # Pulling a full list is 51 requests at best; give it the
                # first half of the progress bar.
                pct = 5 + int(50 * page / EudicClient.MAX_PAGES)
                progress(pct, f"Read {page} pages ({count} words on this page)")

        return self.client.fetch_snapshot(
            scope.bucket, scope.category_id, on_page=on_page
        )

    def build_plan(self, scope, snapshot, mode="twoway"):
        """
        Work out the differences.

        ``mode`` only matters for words that vanished from Eudic: a push
        puts them back (Lute is authoritative) while a pull leaves the
        local term alone.
        """
        plan = SyncPlan(
            remote_total=snapshot.total,
            remote_pages=snapshot.pages_fetched,
            remote_truncated=snapshot.truncated,
            duplicates=len(snapshot.duplicates),
        )
        mapping = self.sync_repo.for_scope(scope.lg_id, scope.bucket, scope.category_id)
        terms = self.term_repo.find_by_statuses(scope.lg_id, PUSHABLE_STATUSES)
        local_keys = set()

        for term in terms:
            key = word_key(term.text)
            if not key:
                plan.skipped += 1
                continue
            local_keys.add(key)
            desired = star_for_status(term.status)
            if desired is None:
                plan.skipped += 1
                continue

            word, inline_reading = split_reading(term.text)
            reading = (term.romanization or "").strip() or inline_reading
            remote = snapshot.words.get(key)
            form = push_form(word, reading, remote_word=remote.word if remote else None)
            item = PushItem(
                term_id=term.id,
                word=form,
                key=key,
                star=desired,
                local_status=int(term.status),
            )
            row = mapping.get(term.id)
            # A row left behind by a failed first attempt has no baseline
            # (both sides still -1); treating it as a real pairing would
            # turn a plain "new word" into a phantom conflict.
            has_baseline = row is not None and row.last_synced_status >= 0

            if not has_baseline:
                if remote is not None and remote.star == desired:
                    # Already on Eudic exactly as we want it: adopt the
                    # pairing instead of spending a request on a no-op.
                    plan.adopt.append(item)
                elif remote is not None and mode == "pull":
                    # Both sides have this word but they have never been
                    # paired, so there is no baseline to say who changed.
                    # A pull means "take Eudic's grading", so honour it.
                    plan.pull_update.append(
                        PullItem(
                            remote=remote,
                            key=key,
                            term_id=term.id,
                            local_status=int(term.status),
                        )
                    )
                else:
                    plan.push_new.append(item)
                continue

            remote_now = remote.star if remote is not None else None
            local_changed = int(term.status) != row.last_synced_status
            remote_changed = remote_now != row.last_synced_star

            if remote_changed and remote_now is None:
                # Deleted on the Eudic side.
                if mode == "pull":
                    plan.unchanged += 1
                else:
                    plan.push_update.append(item)
                continue

            if not local_changed and not remote_changed:
                # Guard against historical drift (e.g. an earlier run
                # recorded a star it never actually applied).
                if remote_now is not None and remote_now != desired:
                    plan.push_update.append(item)
                else:
                    plan.unchanged += 1
                continue

            if local_changed and remote_changed:
                item.conflict = True
                plan.conflicts.append(item)
            elif local_changed:
                plan.push_update.append(item)
            else:
                if int(term.status) in LOCKED_STATUSES:
                    # Well Known stays Well Known even if the star moved.
                    plan.locked += 1
                else:
                    plan.pull_update.append(
                        PullItem(
                            remote=remote,
                            key=key,
                            term_id=term.id,
                            local_status=int(term.status),
                        )
                    )

        for key, remote in snapshot.words.items():
            if key in local_keys:
                continue
            plan.pull_new.append(PullItem(remote=remote, key=key))

        return plan

    # -- execution --------------------------------------------------------

    def sync(self, scope, mode="twoway", progress=None):
        "Run a preview, push, pull or two-way sync."
        started = time.monotonic()
        progress = progress or (lambda pct, msg: None)

        progress(1, "Connecting to Eudic")
        snapshot = self.fetch_snapshot(scope, progress)
        progress(56, "Comparing word lists")
        plan = self.build_plan(scope, snapshot, mode)

        result = SyncResult(
            mode=mode,
            plan=plan.counts(),
            remote_total=plan.remote_total,
            remote_pages=plan.remote_pages,
            remote_truncated=plan.remote_truncated,
            duplicate_keys=plan.duplicates,
            locked=plan.locked,
            skipped=plan.skipped,
        )

        if mode == "preview":
            result.message = "Preview only; nothing was changed."
            result.elapsed_seconds = time.monotonic() - started
            progress(100, "Done")
            return result

        progress(58, "Recording matches")
        self._apply_adopt(plan, scope, result)

        if mode in ("pull", "twoway"):
            progress(60, "Bringing words from Eudic")
            self._apply_pull(plan.pull_new, scope, result, 60, 78, progress)

        if mode in ("push", "twoway"):
            progress(80, "Sending words to Eudic")
            self._apply_push(plan, scope, result, 80, 92, progress)

        if mode in ("pull", "twoway"):
            progress(93, "Updating grades from Eudic")
            self._apply_pull(plan.pull_update, scope, result, 93, 99, progress)

        result.elapsed_seconds = time.monotonic() - started
        result.message = self._summary(result)
        progress(100, "Done")
        return result

    def _summary(self, result):
        "One line for the result panel."
        parts = []
        if result.pushed_new or result.pushed_updated or result.pushed_conflicts:
            parts.append(
                f"sent {result.pushed_new + result.pushed_updated + result.pushed_conflicts}"
            )
        if result.pulled_new or result.pulled_updated:
            parts.append(f"received {result.pulled_new + result.pulled_updated}")
        if result.failure_count:
            parts.append(f"{result.failure_count} failed")
        if not parts:
            parts.append("already in sync")
        if result.aborted_reason:
            parts.append(f"stopped: {result.aborted_reason}")
        return ", ".join(parts) + "."

    def _apply_adopt(self, plan, scope, result):
        "Record pairings that already matched, without any network call."
        if not plan.adopt:
            return
        for item in plan.adopt:
            self.sync_repo.upsert(
                term_id=item.term_id,
                lg_id=scope.lg_id,
                bucket=scope.bucket,
                category_id=scope.category_id,
                remote_word=item.word,
                word_key=item.key,
                remote_star=item.star,
                synced_star=item.star,
                synced_status=item.local_status,
                direction="push",
            )
            result.adopted += 1
        self.session.commit()

    def _apply_push(self, plan, scope, result, start_pct, end_pct, progress):
        "Send new, changed and conflicting terms in throttled batches."
        items = plan.push_new + plan.push_update + plan.conflicts
        if not items:
            return

        # One term per remote key: two local terms can normalise to the
        # same Eudic word (e.g. a term that is literally "俺 [おれ]"), and
        # sending both would just make Eudic keep the first.
        seen = set()
        unique = []
        for item in items:
            if item.key in seen:
                continue
            seen.add(item.key)
            unique.append(item)

        new_ids = {i.term_id for i in plan.push_new}
        conflict_ids = {i.term_id for i in plan.conflicts}
        total = len(unique)

        for offset in range(0, total, PUSH_BATCH_SIZE):
            batch = unique[offset : offset + PUSH_BATCH_SIZE]
            if result.aborted_reason:
                break
            try:
                self.client.add_words(
                    scope.bucket,
                    scope.category_id,
                    [(i.word, i.star) for i in batch],
                )
            except EudicError as e:
                for item in batch:
                    self.sync_repo.record_error(
                        term_id=item.term_id,
                        lg_id=scope.lg_id,
                        bucket=scope.bucket,
                        category_id=scope.category_id,
                        remote_word=item.word,
                        word_key=item.key,
                        message=str(e),
                    )
                    result.failure_count += 1
                    if len(result.failures) < MAX_REPORTED_FAILURES:
                        result.failures.append({"word": item.word, "error": str(e)})
                self.session.commit()
                if e.kind in ("auth", "rate_limit", "not_configured"):
                    # Every further request would fail the same way and
                    # spend the remaining budget doing it.
                    result.aborted_reason = str(e)
                    break
                continue

            for item in batch:
                self.sync_repo.upsert(
                    term_id=item.term_id,
                    lg_id=scope.lg_id,
                    bucket=scope.bucket,
                    category_id=scope.category_id,
                    remote_word=item.word,
                    word_key=item.key,
                    remote_star=item.star,
                    synced_star=item.star,
                    synced_status=item.local_status,
                    direction="push",
                )
                if item.term_id in conflict_ids:
                    result.pushed_conflicts += 1
                elif item.term_id in new_ids:
                    result.pushed_new += 1
                else:
                    result.pushed_updated += 1
            self.session.commit()

            done = offset + len(batch)
            pct = start_pct + int((end_pct - start_pct) * done / total)
            progress(pct, f"Sent {done} of {total} words")

    def _apply_pull(self, items, scope, result, start_pct, end_pct, progress):
        "Create or re-grade local terms from the remote words."
        if not items:
            return
        repo = TermRepository(self.session)
        total = len(items)

        for index, item in enumerate(items, start=1):
            try:
                self._pull_one(repo, item, scope, result)
            except Exception as e:  # pylint: disable=broad-except
                # A single unparseable word must not abort the run.
                result.failure_count += 1
                if len(result.failures) < MAX_REPORTED_FAILURES:
                    result.failures.append({"word": item.remote.word, "error": str(e)})
                self.session.rollback()

            if index % 25 == 0 or index == total:
                pct = start_pct + int((end_pct - start_pct) * index / total)
                progress(pct, f"Imported {index} of {total} words")

    def _pull_one(self, repo, item, scope, result):
        "One remote word -> one local term."
        star = item.remote.star
        status = status_for_star(star)
        if status is None:
            result.skipped += 1
            return

        word, inline_reading = split_reading(item.remote.word)
        reading = (item.remote.phon or "").strip() or inline_reading
        if not word:
            result.skipped += 1
            return

        existing = repo.find(scope.lg_id, word)
        if existing is not None and existing.status in LOCKED_STATUSES:
            # Ignored / Well Known are the user's decisions here; a star
            # change on the Eudic side must not undo them.
            result.locked += 1
            return

        if existing is None:
            term = repo.find_or_new(scope.lg_id, word)
            created = True
        else:
            term = existing
            created = False

        term.status = status
        translation = clean_exp(item.remote.exp)
        if translation and not (term.translation or "").strip():
            # Only fill a blank: the local gloss is usually better than
            # Eudic's, and Japanese lists have no gloss at all.
            term.translation = truncate(translation, 500)
        phon = clean_phon(reading)
        if phon and not (term.romanization or "").strip():
            term.romanization = truncate(phon, 100)
        if item.remote.context_line:
            # The business object carries the flash message as a plain
            # attribute; _build_db_term turns it into wordflashmessages.
            term.flash_message = truncate(item.remote.context_line, 200)

        dbterm = repo.add(term)
        self.session.flush()
        term_id = dbterm.id
        repo.commit()

        if created:
            result.pulled_new += 1
        else:
            result.pulled_updated += 1

        self.sync_repo.upsert(
            term_id=term_id,
            lg_id=scope.lg_id,
            bucket=scope.bucket,
            category_id=scope.category_id,
            remote_word=item.remote.word,
            word_key=item.key,
            remote_star=star,
            synced_star=star,
            synced_status=status,
            remote_add_time=item.remote.add_time or None,
            direction="pull",
        )
        self.session.commit()
