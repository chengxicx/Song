"""
Unit tests for the fragment-merge decisions.

The script's judgement is the risky part -- a wrong "drop" deletes a
word the reader has, and a wrong "carry" invents study history -- so
each rule is pinned here against the production cases that motivated
it.
"""

import os
import sys

# The script under test lives in scripts/, outside the package.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"),
)

from lute.models.term import Status  # noqa: E402
from merge_manga_fragment_terms import (  # noqa: E402
    DROP,
    CARRY,
    SKIP,
    looks_like_debris,
    plan,
)

# Status names only UNKNOWN / IGNORED / WELLKNOWN; the learning steps are
# the bare numbers 1..5 in Status.ALLOWED.
LEARNING_1 = 1


class FakeTerm:
    """Just enough Term for the two decision functions."""

    def __init__(self, text, status):
        self.text = text
        self.text_lc = text.lower()
        self.status = status


def test_bare_numbers_are_never_debris():
    # "80" is a number, not a scrap of "800".
    assert not looks_like_debris("80", "800", Status.UNKNOWN)
    assert not looks_like_debris("16", "160", LEARNING_1)
    assert not looks_like_debris("3", "30", LEARNING_1)


def test_short_cjk_are_real_words_not_debris():
    # These are the production false positives the corpus guard missed:
    # one- and two-character Japanese words.
    assert not looks_like_debris("光", "光っ", LEARNING_1)
    assert not looks_like_debris("火", "火災", LEARNING_1)
    assert not looks_like_debris("口", "口頭", LEARNING_1)
    assert not looks_like_debris("北", "北風", LEARNING_1)
    # Three characters is where cut fragments start to dominate.
    assert looks_like_debris("かわい", "かわいい", Status.UNKNOWN)


def test_short_latin_needs_an_explicit_ignore():
    # "d" and "in" are ordinary words that happen to prefix longer ones.
    assert not looks_like_debris("d", "dvd", Status.UNKNOWN)
    assert not looks_like_debris("in", "instead", Status.UNKNOWN)
    assert not looks_like_debris("no", "not", Status.UNKNOWN)
    # An ignore is the reader's own verdict on that exact string, which
    # is the one case where a short Latin fragment is debris.
    assert looks_like_debris("d", "dvd", Status.IGNORED)
    # Long Latin fragments are broken words.
    assert looks_like_debris("compu", "computer", Status.UNKNOWN)


def test_unknown_fragment_dropped_when_target_known():
    # The production case: プレゼント was learned, プレゼン is status 0.
    frag = FakeTerm("プレゼン", Status.UNKNOWN)
    tgt = FakeTerm("プレゼント", Status.WELLKNOWN)
    action, new_status, _ = plan(frag, tgt)
    assert action == DROP
    assert new_status is None, "an unknown fragment carries no status over"


def test_unknown_fragment_kept_when_target_also_unknown():
    # Merging two words nobody knows would invent a study history.
    action, _, reason = plan(
        FakeTerm("ごらん", Status.UNKNOWN), FakeTerm("ごらんない", Status.UNKNOWN)
    )
    assert action == SKIP
    assert "neither" in reason


def test_ignore_not_imposed_on_an_unknown_target():
    """
    An ignored fragment does NOT make its unknown target ignored.

    This was a real bug found while rehearsing against production: the
    fragment "no" was ignored, its target "not" was unknown, and
    carrying the ignore over would have invented a decision about a
    word the reader has never seen.
    """
    action, _, reason = plan(
        FakeTerm("no", Status.IGNORED), FakeTerm("not", Status.UNKNOWN)
    )
    assert action == SKIP
    assert "invent a decision" in reason


def test_ignore_carries_to_a_known_target():
    # The reader ignored exactly this text, and OCR split it off a word
    # they do know.
    action, new_status, _ = plan(
        FakeTerm("文", Status.IGNORED), FakeTerm("文学", Status.WELLKNOWN)
    )
    assert action == DROP
    assert new_status == Status.IGNORED


def test_learning_status_moves_to_an_unknown_target():
    # Real study happened on the fragment; dropping it would lose that.
    action, new_status, _ = plan(
        FakeTerm("|SWEET", LEARNING_1), FakeTerm("SWEETNESS", Status.UNKNOWN)
    )
    assert action == CARRY
    assert new_status == LEARNING_1


def test_learning_status_not_overwritten():
    # The target already has its own, different progress.
    action, _, reason = plan(
        FakeTerm("x", LEARNING_1), FakeTerm("xyz", Status.WELLKNOWN)
    )
    assert action == SKIP
    assert "already at status" in reason


def test_well_known_fragment_is_not_touched():
    """
    Well Known on the fragment is the reader's own act, not debris.

    "80" being Well Known says they marked that number known; it is not
    an artefact of a cut balloon.
    """
    action, _, reason = plan(
        FakeTerm("80", Status.WELLKNOWN), FakeTerm("800", Status.UNKNOWN)
    )
    assert action == SKIP
    assert "Well Known" in reason
