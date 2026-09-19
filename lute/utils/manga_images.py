"""
Match Mokuro manga pages to the image files extracted on disk.

A page record normally carries an ``img_path``, but not every .mokuro
in the wild does.  The official mokuro CLI *adds* ``img_path`` when it
assembles the volume file (``MokuroGenerator.generate_mokuro_file``),
so a .mokuro assembled straight from the raw ``_ocr`` cache -- or
edited by hand -- has pages holding only
``version / img_width / img_height / blocks``.

That is still recoverable, because mokuro itself does not pair pages
with images by ``img_path``: ``Volume.get_img_paths`` natural-sorts the
image files and keys them by stem, and ``generate_mokuro_file`` simply
writes that pairing down.  These helpers reproduce the same rule, so a
page without ``img_path`` resolves to the right file instead of leaving
the reading screen with nothing to request.
"""

import os
import re

# The extensions mokuro itself accepts when globbing a volume directory
# (see mokuro.volume.Volume.get_img_paths), plus a couple of harmless
# extras so hand-repacked archives still resolve.
IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".avif",
    ".gif",
    ".bmp",
)

_DIGITS = re.compile(r"(\d+)")


def natural_key(path):
    """
    Sort key that orders "2.jpg" before "10.jpg", unlike plain string
    sorting.  Lowercased so ordering does not depend on case.
    """
    normalized = path.replace("\\", "/").lower()
    return [int(part) if part.isdigit() else part for part in _DIGITS.split(normalized)]


def sorted_image_paths(root):
    """
    Every image file under root, as forward-slash paths relative to
    root, in mokuro's page order.

    The whole relative path is natural-sorted, so pages inside a
    volume/ subdirectory stay contiguous and "page2" precedes "page10".
    Returns [] when root is missing or holds no images.
    """
    if not root or not os.path.isdir(root):
        return []

    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
                continue
            abs_path = os.path.join(dirpath, name)
            found.append(os.path.relpath(abs_path, root).replace("\\", "/"))
    found.sort(key=natural_key)
    return found


def image_path_for_page(root, page_index, volume=None):
    """
    Relative path of the image for the page at ``page_index`` (0 based),
    or None when it cannot be determined.

    Pages are matched by position, which is what mokuro does.  When
    ``volume`` names a subdirectory that holds its own images, that
    directory is tried first so a multi-volume archive cannot
    interleave; otherwise (or when the volume holds fewer images than
    the page needs) the whole tree is used.
    """
    if page_index < 0:
        return None

    candidates = []
    if volume:
        vol = volume.strip().replace("\\", "/").strip("/")
        if vol:
            candidates.append((os.path.join(root, vol), f"{vol}/"))
    candidates.append((root, ""))

    for base, prefix in candidates:
        paths = sorted_image_paths(base)
        if 0 <= page_index < len(paths):
            return f"{prefix}{paths[page_index]}"
    return None
