"""
Cache-busting helpers for static assets.

Vendored assets (tagify, datatables, jquery, htmx, ...) are referenced
from base.html with no version in the URL, but they are served with
`Cache-Control: public, max-age=31536000, immutable`, and Cloudflare
caches them too.  That means a stale copy can stick around for a year
even after the file changes on the server.

Adding a ?v=<content hash> to the URL means the URL changes exactly
when the file content changes, so a deploy automatically busts both
the browser cache and the CDN cache -- no manual version bump needed.
"""

import functools
import hashlib
import os

_MISSING = "0"


@functools.lru_cache(maxsize=512)
def file_hash(static_folder, relpath):
    """
    Short sha1 of a static file's content.

    Cached per (static_folder, relpath) so the file is only read once
    per process.  Returns "0" if the file can't be read, so templates
    never blow up on a missing asset.
    """
    if not static_folder:
        return _MISSING
    path = os.path.join(static_folder, relpath)
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return _MISSING


def make_vstatic(static_folder, url_for_func):
    """
    Build the vstatic() template global.

    vstatic('vendor/tagify/tagify.css') ->
        '/static/vendor/tagify/tagify.css?v=<hash>'
    """

    def vstatic(filename):
        return f"{url_for_func(filename)}?v={file_hash(static_folder, filename)}"

    return vstatic
