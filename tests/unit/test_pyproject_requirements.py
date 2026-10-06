"""Guards every pyproject.toml dependency string against a PEP 508 typo.

A malformed specifier does not just break the extra it lives in: pip
rejects the *whole* package at the metadata step, so ``pip install -e .``
installs nothing at all -- no core dependency gets synced.  Nothing in a
normal test run reads pyproject.toml, so the only symptom is a warning
buried in the deploy log (see the ``"fsrs>=5,<7>"`` regression from
ef6a3a6d, where dropping a ``python_version`` marker left the ``>`` of
``>=`` behind and silently disabled dependency sync for every deploy).

The plugins are swept too because deploy.sh installs them editable from
their own pyproject.toml files.

Parsed with the ``toml`` package rather than stdlib ``tomllib``, which
only exists on Python 3.11+ and CI still runs a 3.10 leg.
"""

import glob
from pathlib import Path

import pytest
import toml
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECTS = ["pyproject.toml"] + sorted(glob.glob("plugins/*/pyproject.toml"))


def _read_pyproject(relpath):
    """Return the parsed file, or None if it is missing or malformed."""
    try:
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return toml.loads(text)
    except toml.TomlDecodeError:
        return None


def _collect():
    """(relpath, group, requirement) for every declared dependency."""
    found = []
    for relpath in PYPROJECTS:
        data = _read_pyproject(relpath)
        if data is None:
            continue
        project = data.get("project", {})
        declared = [(None, r) for r in project.get("dependencies", [])]
        for group, reqs in (project.get("optional-dependencies") or {}).items():
            declared += [(group, r) for r in reqs]
        found += [(relpath, group or "core", r) for group, r in declared]
    return found


REQUIREMENTS = _collect()


def test_every_pyproject_parses_and_declares_dependencies():
    """
    The sweep below can only see files it managed to parse, so a broken or
    missing pyproject.toml has to fail loudly here instead of quietly
    shrinking the parametrized list.
    """
    for relpath in PYPROJECTS:
        assert (REPO_ROOT / relpath).is_file(), f"missing {relpath}"
        assert _read_pyproject(relpath) is not None, f"{relpath} is not valid TOML"
    assert REQUIREMENTS, "parsed no dependencies at all -- did pyproject.toml move?"


@pytest.mark.parametrize(
    "relpath,group,requirement",
    REQUIREMENTS,
    ids=[f"{p}:{g}:{r}" for p, g, r in REQUIREMENTS],
)
def test_requirement_is_a_valid_pep508_specifier(relpath, group, requirement):
    # Raises InvalidRequirement (with a caret pointing at the bad character)
    # if the string is malformed.
    Requirement(requirement)
