"""
Locate a plugin folder on disk from just its directory name.

Browsers expose only ``webkitRelativePath`` (e.g. ``lute-storygen/pyproject.toml``),
so when the user picks a folder we only learn its top-level name.  This module
searches a small, curated set of likely locations (not the whole home
directory, which is slow and flaky) and returns the matching absolute path,
falling back to ``None``.
"""

import os


def _search_roots():
    """Candidate roots to search, ordered by likelihood."""
    roots = []
    home = os.path.expanduser("~")

    env_dir = os.environ.get("LUTE_PLUGINS_DIR")
    if env_dir:
        roots.append(env_dir)

    roots.append(os.path.abspath(os.path.join(os.getcwd(), "plugins")))
    roots.append(os.path.join(os.getcwd(), ".."))  # sibling of the checkout

    roots.append(os.path.join(home, "Documents", "lutedev"))
    roots.append(os.path.join(home, "Documents"))
    roots.append(os.path.join(home, "Downloads"))

    # Deduplicate while preserving order.
    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def find_plugin_dir(name):
    """
    Return the absolute path of a directory named ``name`` if it can be
    uniquely located within the curated search roots; otherwise None.

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
        # Only descend two levels under each root — plugins live at
        # <root>/<name> or <root>/<something>/<name> at most.
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, _filenames in os.walk(root):
            depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= 3:
                dirnames[:] = []
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
