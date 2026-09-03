"""
Locate a plugin folder on disk from just its directory name.

Browsers expose only ``webkitRelativePath`` (e.g. ``lute-storygen/pyproject.toml``),
so when the user picks a folder we only learn its top-level name.  This module
searches the locations a Lute user is most likely to keep a local plugin and
returns the matching absolute path, falling back to ``None``.
"""

import os


def _search_roots():
    """Candidate roots to search, ordered by likelihood."""
    return [os.path.expanduser("~")]


def find_plugin_dir(name):
    """
    Return the absolute path of a directory named ``name`` if it can be
    uniquely located; otherwise return None.

    Only directories that look like a Python package root (contain a
    ``pyproject.toml`` or ``setup.py``) are considered, to avoid matching
    arbitrary folders.
    """
    name = (name or "").strip()
    if not name or name in ("pyproject.toml", "setup.py", "setup.cfg", "src"):
        return None

    matches = []
    for root in _search_roots():
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, _filenames in os.walk(root):
            # Skip heavy / hidden trees.
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in ("node_modules", "venv", ".venv", "site-packages", "__pycache__", "dist", "build", "Pods", ".git")
            ]
            if dirpath == root:
                continue
            if os.path.basename(dirpath) == name and _looks_like_plugin(dirpath):
                matches.append(dirpath)
                if len(matches) > 1:
                    return None  # ambiguous — ask the user for the full path
    return matches[0] if matches else None


def _looks_like_plugin(dirpath):
    """A plugin folder should contain a packaging file."""
    for f in ("pyproject.toml", "setup.py", "setup.cfg"):
        if os.path.isfile(os.path.join(dirpath, f)):
            return True
    return False
