"""
Where reader-mode EPUB packages live.

An extracted package is user data, not code: it sits next to the
databases, under `<EPUB_STATIC_ROOT>/epub/<uuid>/`, so the deploy-time
backup and a restore carry the chapters along with the books that point
at them.  It used to be written under the application's static folder,
which `git clean` removes and nothing backs up -- restoring a database
then left every reader-mode book aiming at files that were gone.

The URL is still `/static/epub/...`: the stored chapter markup has that
prefix baked into its rewritten `src` / `href` attributes and into the
scoped stylesheet's `url()` references, so changing it would break the
books that already exist.

The legacy location stays readable (`legacy_root`) so a server that has
upgraded but not yet run deploy.sh's relocation step keeps serving its
chapters; `located()` prefers the data directory.
"""

import os

# Stored paths (`BkEpubPath`) are relative to the static root and start
# with this segment: "epub/<uuid>/ch/000.html".
PREFIX = "epub"


def package_root(config):
    "The directory holding one subdirectory per extracted package."
    return os.path.join(config["EPUB_STATIC_ROOT"], PREFIX)


def legacy_root(app):
    "Where packages were written before the move (read-only fallback)."
    return os.path.join(app.static_folder, PREFIX)


def located(app, stored_path):
    """
    Absolute path of an extracted file, or None if it is not there.

    `stored_path` is either a stored `BkEpubPath`
    ("epub/<uuid>/ch/000.html") or the part after `epub/` (what a
    `/static/epub/...` URL carries); both forms are accepted.  Paths
    containing `..` are rejected rather than resolved: a book's stored
    path never has one, and this is the reading page's only door to the
    filesystem.
    """
    rel = (stored_path or "").lstrip("/")
    if rel.startswith(PREFIX + "/"):
        rel = rel[len(PREFIX) + 1 :]
    if not rel or ".." in rel.split("/"):
        return None
    for root in (package_root(app.config), legacy_root(app)):
        candidate = os.path.join(root, *rel.split("/"))
        if os.path.isfile(candidate):
            return candidate
    return None
