"""
FSRS scheduler wrapper for the review queue.

The official `fsrs` package (py-fsrs) is an optional dependency: it
requires Python >= 3.10, so it is pip-installed on demand -- mirroring
the grammar-engine install pattern (status line + one-click install
button) -- and the review pages degrade gracefully without it.

All fsrs imports are lazy: enqueueing (criteria + sentence lookups)
works without the package; only scheduling a review needs it.
"""

import importlib.metadata
import importlib.util
import subprocess
import sys
from datetime import timezone

# py-fsrs 4.x was the first release under the 'fsrs' name on PyPI;
# anything below that is an abandoned unrelated package.
_FSRS_MIN_MAJOR = 4
_FSRS_PIP_SPEC = "fsrs>=5,<7"
_PIP_TIMEOUT_SECONDS = 900


class SchedulerUnavailableError(Exception):
    "The fsrs package is not installed (or is too old to use)."


def fsrs_status():
    """
    UI summary of the scheduler availability:
      {"installed", "version", "installable", "pip_spec"}
    """
    version = _installed_version()
    return {
        "installed": version is not None,
        "version": version,
        "installable": True,
        "pip_spec": _FSRS_PIP_SPEC,
    }


def _installed_version():
    "Version of a usable fsrs package, or None."
    if importlib.util.find_spec("fsrs") is None:
        return None
    try:
        raw = importlib.metadata.version("fsrs")
    except importlib.metadata.PackageNotFoundError:
        return None
    major = raw.split(".")[0]
    if not major.isdigit() or int(major) < _FSRS_MIN_MAJOR:
        return None
    return raw


def install_fsrs():
    """
    pip-install the fsrs package.

    Returns (ok, message).  Mirrors install_grammar_engine: the running
    app can usually import the new package without a restart.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", _FSRS_PIP_SPEC],
            capture_output=True,
            text=True,
            timeout=_PIP_TIMEOUT_SECONDS,
            check=False,  # the returncode is handled below
        )
    except subprocess.TimeoutExpired:
        return False, "pip install of fsrs timed out"
    except OSError as e:
        return False, f"Could not run pip: {e}"
    if proc.returncode != 0:
        output = (proc.stdout or "") + (proc.stderr or "")
        return False, f"pip install of fsrs failed:\n{output.strip()[-2000:]}"
    if _installed_version() is None:
        return (
            False,
            (
                "fsrs was installed but cannot be used with this Python "
                f"({sys.version_info.major}.{sys.version_info.minor}); "
                "fsrs requires Python >= 3.10."
            ),
        )
    return True, "Installed the fsrs scheduler package."


def load_scheduler(desired_retention):
    """
    Build a Scheduler with the user's settings.

    Raises SchedulerUnavailableError if fsrs is missing or too old.
    """
    try:
        # Lazy import: fsrs is an optional dependency.
        from fsrs import Scheduler  # pylint: disable=import-outside-toplevel
    except ImportError as ex:
        raise SchedulerUnavailableError("The fsrs package is not installed.") from ex
    retention = _clamp_retention(desired_retention)
    return Scheduler(desired_retention=retention)


def _clamp_retention(desired_retention):
    "Keep the retention in a sane range, defaulting on garbage."
    try:
        r = float(desired_retention)
    except (TypeError, ValueError):
        return 0.9
    return min(0.99, max(0.5, r))


def load_card(dbcard):
    """
    Build the fsrs.Card matching a ReviewCard row.

    New/unreviewed cards come back as a fresh Card (due immediately);
    previously-reviewed cards are rebuilt from the stored state.
    """
    from fsrs import (  # pylint: disable=import-outside-toplevel,import-error
        Card,
        State,
    )

    if dbcard.reps == 0:
        return Card()

    return Card(
        state=State(dbcard.state),
        stability=dbcard.stability,
        difficulty=dbcard.difficulty,
        due=_aware(dbcard.due),
        last_review=_aware(dbcard.last_review),
    )


def save_card(dbcard, fcard, rating_int):
    """
    Copy fsrs card state back onto the ReviewCard row.

    reps is Lute-side (fsrs v6 dropped it): it counts gradings and marks
    a card as no longer new.  lapses counts Again grades.
    """
    dbcard.due = _naive(fcard.due)
    dbcard.state = int(fcard.state)
    dbcard.stability = fcard.stability
    dbcard.difficulty = fcard.difficulty
    dbcard.last_review = (
        _naive(fcard.last_review) if fcard.last_review is not None else None
    )
    dbcard.reps = (dbcard.reps or 0) + 1
    if rating_int == 1:
        dbcard.lapses = (dbcard.lapses or 0) + 1


def rating_value(rating_int):
    "Map 1-4 to fsrs.Rating."
    from fsrs import (  # pylint: disable=import-outside-toplevel,import-error
        Rating,
    )

    return {
        1: Rating.Again,
        2: Rating.Hard,
        3: Rating.Good,
        4: Rating.Easy,
    }[rating_int]


def next_intervals(scheduler, fcard, now):
    """
    Preview the next due intervals for each rating, as display strings
    [Again, Hard, Good, Easy].  Does not mutate fcard.
    """
    import copy  # pylint: disable=import-outside-toplevel

    labels = []
    for rating_int in (1, 2, 3, 4):
        clone = copy.deepcopy(fcard)
        newcard, _log = scheduler.review_card(clone, rating_value(rating_int), now)
        labels.append(_format_interval(newcard.due - now))
    return labels


def _format_interval(delta):
    "Human-readable interval, Anki-style."
    seconds = delta.total_seconds()
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{round(seconds / 60)}m"
    if seconds < 86400:
        return f"{_fmt(seconds / 3600)}h"
    days = seconds / 86400
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{round(days / 30)}mo"
    return f"{_fmt(days / 365)}y"


def _fmt(value):
    "Whole numbers print without a decimal: 2 not 2.0."
    whole = round(value)
    if abs(value - whole) < 0.05:
        return str(whole)
    return f"{value:.1f}"


def log_snapshot(flog):
    "JSON snapshot of a py-fsrs ReviewLog, for future optimizer imports."
    try:
        return flog.to_json()
    except (AttributeError, TypeError, ValueError):
        return None


def _aware(dt):
    "Naive-UTC db datetime -> UTC-aware, as fsrs requires."
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _naive(dt):
    "UTC-aware datetime -> naive UTC for db storage."
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
