"""Tests for the 3D insight data builders.

The builders only ever call ``session.execute(...).all()``, so a stub
session is enough to exercise the bucketing and grid maths without
creating review/book rows.
"""

from types import SimpleNamespace

import pytest

from lute.insight import service


class FakeSession:
    "Returns canned rows for any query."

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(params or {})
        return self

    def all(self):
        return self.rows


# --- retention surface ----------------------------------------------------


def test_retention_surface_is_empty_without_samples(monkeypatch):
    monkeypatch.setattr(service, "retention_samples", lambda session, lid: [])
    data = service.retention_surface(FakeSession([]), 1)
    assert data["cells"] == []
    assert data["empty"] is True
    assert data["samples"] == 0
    # The theory grid is always complete, so the sheet still draws.
    assert len(data["theory"]) == (len(service.STABILITY_EDGES) - 1) * (
        len(service.ELAPSED_EDGES) - 1
    )


def test_retention_surface_averages_the_cell_and_respects_the_sample_floor(monkeypatch):
    # Five recalled + one forgotten, all in the same bucket, is enough.
    samples = [(5.0, 2.0, True)] * 5 + [(5.0, 2.0, False)]
    monkeypatch.setattr(service, "retention_samples", lambda session, lid: samples)
    data = service.retention_surface(FakeSession([]), 1)
    assert len(data["cells"]) == 1
    cell = data["cells"][0]
    assert cell["n"] == 6
    assert cell["z"] == pytest.approx(5 / 6, abs=0.001)


def test_retention_surface_drops_cells_below_the_floor(monkeypatch):
    samples = [(5.0, 2.0, True)] * (service.MIN_CELL_SAMPLES - 1)
    monkeypatch.setattr(service, "retention_samples", lambda session, lid: samples)
    data = service.retention_surface(FakeSession([]), 1)
    assert data["cells"] == []
    assert data["empty"] is True


def test_retention_surface_buckets_by_both_axes(monkeypatch):
    samples = [(1.0, 1.0, True)] * 6 + [(400.0, 300.0, False)] * 6
    monkeypatch.setattr(service, "retention_samples", lambda session, lid: samples)
    data = service.retention_surface(FakeSession([]), 1)
    keys = {(c["x"], c["y"]) for c in data["cells"]}
    assert len(keys) == 2
    xs = [c["x"] for c in data["cells"]]
    assert max(xs) == len(service.STABILITY_EDGES) - 2


def test_theory_surface_decays_with_elapsed_days():
    "The FSRS sheet must slope: higher retention sooner after a review."
    same_stability = [service._fsrs_retention(e, 30.0) for e in (0.5, 7, 60, 365)]
    assert same_stability[0] > same_stability[1] > same_stability[2] > same_stability[3]


def test_bucket_labels_cover_the_edges():
    labels = [
        service._bucket_label(service.STABILITY_EDGES, i)
        for i in range(len(service.STABILITY_EDGES) - 1)
    ]
    assert labels[0] == "0-0d"
    assert labels[-1] == ">=365d"


# --- galaxy ---------------------------------------------------------------


def _word_row(wo_id, status, tokens, jd):
    return (wo_id, status, tokens, jd)


def test_galaxy_is_empty_without_words():
    data = service.lexicon_galaxy(FakeSession([]), 1)
    assert data["empty"] is True
    assert data["days"] == []


def test_galaxy_normalises_days_against_the_earliest_word():
    rows = [
        _word_row(1, 3, 1, 2459000.0),
        _word_row(2, 5, 4, 2459010.0),
        _word_row(3, 99, 2, 2459005.0),
    ]
    data = service.lexicon_galaxy(FakeSession(rows), 1)
    assert data["total"] == 3
    assert data["days"] == [0, 10, 5]
    assert data["status"] == [3, 5, 99]
    assert data["max_day"] == 10


def test_galaxy_thins_deterministically(monkeypatch):
    rows = [_word_row(i, 2, 1, 2459000.0 + i) for i in range(10)]
    first = service.lexicon_galaxy(FakeSession(rows), 1, limit=4)
    second = service.lexicon_galaxy(FakeSession(rows), 1, limit=4)
    assert first["shown"] == len(first["days"]) <= 4
    assert first["total"] == 10
    assert first["days"] == second["days"]


# --- terrain --------------------------------------------------------------


def test_terrain_aligns_the_grid_to_monday():
    # 2026-11-02 is a Monday, 11-08 the Sunday after it.
    rows = [("2026-11-02", 100), ("2026-11-08", 50), ("2026-11-09", 25)]
    # An 8-day window ends on 11-09, so 11-02 is the Monday it starts on.
    data = service.reading_terrain(FakeSession(rows), 1, days=8)
    assert data["empty"] is False
    assert data["first_date"] == "2026-11-02"
    assert data["weeks"][0][0] == 100  # Monday
    assert data["weeks"][0][6] == 50  # Sunday of the same week
    assert data["weeks"][1][0] == 25  # next Monday
    assert data["max"] == 100
    assert data["total"] == 175


def test_terrain_is_empty_without_reads():
    data = service.reading_terrain(FakeSession([]), 1)
    assert data["empty"] is True
    assert data["weeks"] == []


def test_terrain_days_without_reading_are_zero():
    # A 3-day window ending on Wednesday 11-04 starts on Monday 11-02.
    rows = [("2026-11-02", 10), ("2026-11-04", 20)]
    data = service.reading_terrain(FakeSession(rows), 1, days=3)
    week = data["weeks"][0]
    assert week[0] == 10
    assert week[1] == 0
    assert week[2] == 20


# --- level towers ---------------------------------------------------------


def test_level_towers_reports_unsupported_for_an_unknown_language():
    data = service.level_towers(FakeSession([]), 1, "Klingon")
    assert data["supported"] is False
    assert data["levels"] == []


def test_level_towers_bands_each_level_by_status(monkeypatch):
    fake_module = SimpleNamespace(
        LEVELS=["L1"],
        level_totals=lambda: {"L1": 100},
        fake_level=lambda word: "L1",
    )
    monkeypatch.setitem(
        service._LEVEL_SOURCES, "fakeish", (fake_module, "fake_level", None)
    )

    rows = [("alpha", 1), ("beta", 1), ("gamma", 99), ("delta", 98)]
    data = service.level_towers(FakeSession(rows), 1, "Fakeish")
    assert data["supported"] is True
    level = data["levels"][0]
    assert level["total"] == 100
    assert level["bands"]["1"] == 2
    assert level["bands"]["99"] == 1
    assert level["bands"]["98"] == 1
    assert level["studied"] == 3  # Ignored is not "studied"


def test_level_towers_ignores_words_outside_every_level(monkeypatch):
    fake_module = SimpleNamespace(
        LEVELS=["L1"],
        level_totals=lambda: {"L1": 10},
        fake_level=lambda word: None,
    )
    monkeypatch.setitem(
        service._LEVEL_SOURCES, "fakeish", (fake_module, "fake_level", None)
    )
    data = service.level_towers(FakeSession([("alpha", 2)]), 1, "Fakeish")
    assert data["levels"][0]["bands"]["2"] == 0
    assert data["empty"] is True
