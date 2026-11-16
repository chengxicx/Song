"""Tests for the Eudic reconciliation engine."""

import pytest

from lute.db import db
from lute.eudic.client import EudicError, RemoteSnapshot, RemoteWord
from lute.eudic.models import EudicSyncRepository
from lute.eudic.service import EudicService, SyncScope
from lute.eudic.mapping import word_key
from lute.term.model import Repository as TermRepository


class FakeClient:
    "Records writes; replays a canned snapshot."

    def __init__(self, snapshot=None, fail_first_batches=0):
        self.snapshot = snapshot or RemoteSnapshot(bucket="en", category_id="0")
        self.added = []
        self.fail_first_batches = fail_first_batches
        self.pages_read = 0

    def fetch_snapshot(self, bucket, category_id, on_page=None):
        self.pages_read += 1
        if on_page is not None:
            on_page(1, len(self.snapshot.words))
        return self.snapshot

    def add_words(self, bucket, category_id, items):
        if self.fail_first_batches:
            self.fail_first_batches -= 1
            raise EudicError(
                "Eudic could not save the data.", kind="server", status=500
            )
        batch = list(items)
        self.added.append(batch)
        # A real server would echo these back on the next read, which is
        # what makes a second push a no-op; keep the fake in step.
        for word, star in batch:
            self.snapshot.words[word_key(word)] = RemoteWord(word=word, star=star)
        return "ok"

    def list_categories(self, bucket):
        return []


def _add_term(lang, text, status=1, translation=None, reading=None):
    "Create a term through the business layer, like the app does."
    repo = TermRepository(db.session)
    t = repo.find_or_new(lang.id, text)
    t.status = status
    if translation:
        t.translation = translation
    if reading:
        t.romanization = reading
    dbterm = repo.add(t)
    repo.commit()
    return dbterm.id


def _baseline(lang, term_id, star, status, remote_word, category_id="0"):
    "Seed a row as if a previous run had synced this word."
    EudicSyncRepository(db.session).upsert(
        term_id=term_id,
        lg_id=lang.id,
        bucket="en",
        category_id=category_id,
        remote_word=remote_word,
        word_key=word_key(remote_word),
        remote_star=star,
        synced_star=star,
        synced_status=status,
        direction="push",
    )
    db.session.commit()


def _snapshot(*words):
    "Build a snapshot from (word, star) pairs."
    snap = RemoteSnapshot(bucket="en", category_id="0")
    for word, star in words:
        rw = RemoteWord(word=word, star=star)
        snap.words[word_key(word)] = rw
    return snap


@pytest.fixture(name="scope")
def fixture_scope(english):
    return SyncScope(
        lg_id=english.id,
        language_name="English",
        bucket="en",
        category_id="0",
    )


# --- build_plan -----------------------------------------------------------


def test_local_only_word_is_pushed(english, scope):
    _add_term(english, "apple", status=2)
    plan = EudicService(db.session, FakeClient()).build_plan(scope, _snapshot())
    assert [i.word for i in plan.push_new] == ["apple"]
    assert plan.push_new[0].star == 2


def test_already_matching_word_is_adopted_not_re_sent(english, scope):
    "First run against a populated studylist must not re-upload everything."
    _add_term(english, "apple", status=3)
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("apple", 3))
    )
    assert plan.adopt and not plan.push_new
    assert plan.adopt[0].word == "apple"


def test_remote_only_word_is_pulled(english, scope):
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("banana", 2))
    )
    assert [i.remote.word for i in plan.pull_new] == ["banana"]


def test_local_change_is_pushed(english, scope):
    term_id = _add_term(english, "apple", status=4)
    _baseline(english, term_id, star=2, status=2, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("apple", 2))
    )
    assert [i.star for i in plan.push_update] == [4]


def test_remote_change_is_pulled(english, scope):
    term_id = _add_term(english, "apple", status=2)
    _baseline(english, term_id, star=2, status=2, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("apple", 5))
    )
    assert not plan.push_update
    assert [i.remote.star for i in plan.pull_update] == [5]


def test_no_changes_means_nothing_to_do(english, scope):
    term_id = _add_term(english, "apple", status=2)
    _baseline(english, term_id, star=2, status=2, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("apple", 2))
    )
    assert plan.unchanged == 1
    assert not any([plan.push_new, plan.push_update, plan.pull_update, plan.conflicts])


def test_both_sides_changed_is_a_conflict_won_by_lute(english, scope):
    term_id = _add_term(english, "apple", status=4)
    _baseline(english, term_id, star=2, status=2, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("apple", 5))
    )
    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].star == 4
    assert not plan.pull_update


def test_well_known_is_pushed_as_the_top_star(english, scope):
    _add_term(english, "apple", status=99)
    plan = EudicService(db.session, FakeClient()).build_plan(scope, _snapshot())
    assert plan.push_new[0].star == 5


def test_ignored_words_are_never_pushed(english, scope):
    _add_term(english, "apple", status=98)
    plan = EudicService(db.session, FakeClient()).build_plan(scope, _snapshot())
    assert not plan.push_new


def test_unknown_words_are_never_pushed(english, scope):
    _add_term(english, "apple", status=0)
    plan = EudicService(db.session, FakeClient()).build_plan(scope, _snapshot())
    assert not plan.push_new


def test_pull_never_downgrades_a_well_known_word(english, scope):
    term_id = _add_term(english, "apple", status=99)
    _baseline(english, term_id, star=5, status=99, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(("apple", 2))
    )
    assert plan.locked == 1
    assert not plan.pull_update


def test_word_deleted_on_eudic_is_readded_by_push(english, scope):
    term_id = _add_term(english, "apple", status=3)
    _baseline(english, term_id, star=3, status=3, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(), mode="push"
    )
    assert [i.word for i in plan.push_update] == ["apple"]


def test_word_deleted_on_eudic_is_left_alone_by_pull(english, scope):
    term_id = _add_term(english, "apple", status=3)
    _baseline(english, term_id, star=3, status=3, remote_word="apple")
    plan = EudicService(db.session, FakeClient()).build_plan(
        scope, _snapshot(), mode="pull"
    )
    assert not plan.push_update
    assert plan.unchanged == 1


# --- execution ------------------------------------------------------------


def test_push_records_the_baseline_so_the_next_run_is_a_no_op(english, scope):
    _add_term(english, "apple", status=3)
    client = FakeClient()
    service = EudicService(db.session, client)

    first = service.sync(scope, mode="push")
    assert first.pushed_new == 1
    assert client.added == [[("apple", 3)]]

    second = service.sync(scope, mode="push")
    assert second.pushed_new == 0
    assert len(client.added) == 1


def test_push_failure_does_not_advance_the_baseline(english, scope):
    "A failed batch must be retried, not marked as synced."
    _add_term(english, "apple", status=3)
    client = FakeClient(fail_first_batches=1)
    service = EudicService(db.session, client)

    first = service.sync(scope, mode="push")
    assert first.failure_count == 1
    assert first.pushed_new == 0

    second = service.sync(scope, mode="push")
    assert second.pushed_new == 1


def test_pull_creates_a_term_with_status_and_translation(english, scope):
    snapshot = RemoteSnapshot(bucket="en", category_id="0")
    snapshot.words["apple"] = RemoteWord(
        word="apple", star=4, exp="n. 苹果", phon="/ˈæpl/"
    )
    service = EudicService(db.session, FakeClient(snapshot))
    result = service.sync(scope, mode="pull")
    assert result.pulled_new == 1

    term = TermRepository(db.session).find(english.id, "apple")
    assert term is not None
    assert term.status == 4
    assert term.translation == "n. 苹果"
    assert term.romanization == "ˈæpl"


def test_pull_does_not_overwrite_a_local_translation(english, scope):
    _add_term(english, "apple", status=1, translation="我自己写的释义")
    snapshot = RemoteSnapshot(bucket="en", category_id="0")
    snapshot.words["apple"] = RemoteWord(word="apple", star=2, exp="n. 苹果")
    service = EudicService(db.session, FakeClient(snapshot))
    service.sync(scope, mode="pull")

    term = TermRepository(db.session).find(english.id, "apple")
    assert term.translation == "我自己写的释义"
    assert term.status == 2


def test_pull_parses_the_bracketed_reading(english, scope):
    "Eudic stores 俺 [おれ]; Lute must keep the word and the reading apart."
    snapshot = RemoteSnapshot(bucket="en", category_id="0")
    snapshot.words["俺"] = RemoteWord(word="俺 [おれ]", star=2)
    service = EudicService(db.session, FakeClient(snapshot))
    service.sync(scope, mode="pull")

    term = TermRepository(db.session).find(english.id, "俺")
    assert term is not None
    assert term.text == "俺"
    assert term.romanization == "おれ"


def test_pull_is_idempotent(english, scope):
    snapshot = RemoteSnapshot(bucket="en", category_id="0")
    snapshot.words["apple"] = RemoteWord(word="apple", star=2)
    service = EudicService(db.session, FakeClient(snapshot))
    service.sync(scope, mode="pull")
    second = service.sync(scope, mode="pull")
    assert second.pulled_new == 0
    assert second.pulled_updated == 0


def test_pull_records_the_pairing_so_push_does_not_re_send(english, scope):
    snapshot = RemoteSnapshot(bucket="en", category_id="0")
    snapshot.words["apple"] = RemoteWord(word="apple", star=2)
    client = FakeClient(snapshot)
    service = EudicService(db.session, client)
    service.sync(scope, mode="pull")
    service.sync(scope, mode="push")
    assert client.added == []


def test_preview_changes_nothing(english, scope):
    _add_term(english, "apple", status=3)
    client = FakeClient()
    result = EudicService(db.session, client).sync(scope, mode="preview")
    assert result.plan["push_new"] == 1
    assert client.added == []
    assert EudicSyncRepository(db.session).for_scope(english.id, "en", "0") == {}


def test_push_sends_the_bracketed_form_for_a_reading(english, scope):
    _add_term(english, "俺", status=2, reading="おれ")
    client = FakeClient()
    EudicService(db.session, client).sync(scope, mode="push")
    assert client.added == [[("俺 [おれ]", 2)]]


def test_push_reuses_the_remote_string_verbatim(english, scope):
    "Never invent a second spelling for a word Eudic already has."
    term_id = _add_term(english, "俺", status=3, reading="おれ")
    _baseline(english, term_id, star=2, status=2, remote_word="俺 [おれ]")
    client = FakeClient()
    EudicService(db.session, client).sync(scope, mode="push")
    assert client.added == [[("俺 [おれ]", 3)]]


def test_truncated_snapshot_is_reported(english, scope):
    snapshot = _snapshot(("apple", 2))
    snapshot.truncated = True
    snapshot.pages_fetched = 51
    result = EudicService(db.session, FakeClient(snapshot)).sync(scope, mode="preview")
    assert result.remote_truncated is True
    assert result.remote_pages == 51
