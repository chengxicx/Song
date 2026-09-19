"""
Empty marker for the feature-plugin entry point.

Lute's own ``pyproject.toml`` registers this as a ``lute.plugin.feature``
entry point so the loader has at least one plugin to iterate over
even when no external feature plugin is installed.  This module
performs no work; its presence proves the wiring is functional.

Real feature plugins (e.g. ``lute3-storygen``) live in separate
Python packages and are installed via pip or dropped into a
``plugins/`` directory.  See ``lute/feature/__init__.py`` for the
contract that plugins must implement.
"""


def register(app):  # pylint: disable=unused-argument
    """No-op: lute core ships no feature plugins."""
    return None