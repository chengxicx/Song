"""
/read/grammar_analysis under concurrency -- the 500 the panel used to answer.

Lute serves requests from a waitress thread pool (12 threads by default --
see lute/main.py), so two readers on the same Japanese page, or one reader
double-clicking the panel, tokenize at the same time.  sudachipy's Tokenizer
wraps a mutable Rust object and cannot be shared: concurrent tokenize() calls
raise RuntimeError("Already borrowed"), which the route does not catch, so
the panel answered 500 instead of grammar points.

The second test restores the old implementation on purpose.  A concurrency
test that only asserts "no 500" proves nothing if the race simply never
happened, so this one pins the failing direction too -- and it is what makes
the passing one meaningful.

The whole file needs the Japanese engine, so it skips without it -- like
every other grammar-engine test here.  Note that CI installs its
dependencies with `flit install --deps develop`, which installs the
dev/doc/test extras but *not* this project's own extras, so these tests do
not run on CI at all; they run against a developer venv (and would run on CI
if that install step asked for the extras).
"""

import json
import threading

import pytest

pytest.importorskip("sudachipy")
pytest.importorskip("sudachidict_core")

from lute.db import db
from lute.parse.registry import is_supported
from lute.read.render import grammar_analysis_ja as grammar_ja
from tests.utils import make_book

TEXTS = [
    "この本は高いですが、面白いです。日本語が好きですが、難しいです。",
    "今、ご飯を食べています。日本に行きたいです。",
]

N_THREADS = 8
N_ITER = 15


@pytest.fixture(name="ja_book")
def fixture_ja_book(app_context, japanese):
    "A Japanese book whose page 1 carries the grammar sample text."
    if not is_supported("japanese_sudachi"):
        pytest.skip("japanese_sudachi parser not installed")
    book = make_book("Grammar concurrency", TEXTS, japanese)
    db.session.add(book)
    db.session.commit()
    return book


def _hammer(app, book_id):
    "Fire concurrent requests; return (statuses, errors, bodies)."
    statuses, errors, bodies = [], [], []
    lock = threading.Lock()
    barrier = threading.Barrier(N_THREADS)

    def work():
        client = app.test_client()
        barrier.wait()
        for _ in range(N_ITER):
            try:
                resp = client.get(f"/read/grammar_analysis/{book_id}/1")
                with lock:
                    statuses.append(resp.status_code)
                    if resp.status_code == 200:
                        bodies.append(resp.data.decode("utf-8"))
            except Exception as e:  # pylint: disable=broad-except
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")
                return

    threads = [threading.Thread(target=work) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "worker thread hung"
    return statuses, errors, bodies


def test_fixed_route_answers_200_under_concurrency(app, ja_book):
    "The fix: every concurrent request answers 200 with the same JSON."
    statuses, errors, bodies = _hammer(app, ja_book.id)
    assert not errors, f"request raised: {errors[:3]}"
    assert statuses, "no responses at all"
    assert set(statuses) == {200}, f"non-200 responses: {sorted(set(statuses))}"
    assert len(set(bodies)) == 1, "responses disagreed"
    data = json.loads(bodies[0])
    assert data, "grammar panel came back empty"


def test_old_route_answers_500_under_concurrency(app, ja_book, monkeypatch):
    "The bug: restore the old unlocked shared tokenizer -> 500s."
    # Tests run with TESTING=True, which re-raises view exceptions instead
    # of turning them into responses.  Production does not, so turn both
    # off to see what a real deployment would answer.
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    state = {"tok": None}

    def buggy():
        if state["tok"] is None:
            from sudachipy import Dictionary  # pylint: disable=import-outside-toplevel

            state["tok"] = Dictionary().create()
        return state["tok"]

    monkeypatch.setattr(grammar_ja, "_get_tokenizer", buggy)
    statuses, errors, _ = _hammer(app, ja_book.id)
    assert not errors, f"request raised: {errors[:3]}"
    assert 500 in statuses, (
        "expected the old implementation to 500 under concurrency, "
        f"got {sorted(set(statuses))}"
    )
