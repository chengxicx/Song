"""
Feature-suite hooks.
"""

import pytest

from lute.parse.registry import is_supported


def pytest_collection_modifyitems(config, items):
    "Skip tagged scenarios when the parser they pin down is not installed."
    if is_supported("japanese_sudachi"):
        return
    skip = pytest.mark.skip(
        reason="scenario asserts Sudachi tokenization; sudachi extra not installed"
    )
    for item in items:
        if item.get_closest_marker("skip_without_sudachi"):
            item.add_marker(skip)
