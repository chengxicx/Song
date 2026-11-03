"""
Thumbnail handling for the user images shown in term cards.

Images are stored as downloaded (the original file is never modified).
A WebP thumbnail is derived next to it, named ``<original>_thumb.webp``,
so no database column is needed: the path is derived from the filename
that is already stored in ``wordimages.WiSource``.
"""

import logging
import os
import tempfile

from PIL import Image, ImageOps, UnidentifiedImageError

THUMBNAIL_MAX_EDGE = 400
THUMBNAIL_QUALITY = 80
THUMBNAIL_SUFFIX = "_thumb.webp"

logger = logging.getLogger(__name__)


def thumbnail_filename(filename):
    "The thumbnail file name for a stored image file name."
    stem = filename.rpartition(".")[0] or filename
    return f"{stem}{THUMBNAIL_SUFFIX}"


def thumbnail_path_for(original_path):
    "The thumbnail path for an original image path (same directory)."
    return os.path.join(
        os.path.dirname(original_path),
        thumbnail_filename(os.path.basename(original_path)),
    )


def full_url(langid, filename):
    "URL of the stored original."
    return f"/userimages/{langid}/{filename}"


def thumbnail_url(langid, filename):
    "URL of the derived thumbnail; served on demand if not yet generated."
    return f"/userimages/{langid}/thumb/{filename}"


def _write_thumbnail(original_path, thumb_path):
    "Render the WebP thumbnail and atomically swap it into place."
    with Image.open(original_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(
            (THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), Image.Resampling.LANCZOS
        )
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
        else:
            image = image.convert("RGB")

        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(thumb_path), suffix=".webp.tmp"
        )
        os.close(fd)
        try:
            image.save(temp_path, "WEBP", quality=THUMBNAIL_QUALITY, method=4)
            os.replace(temp_path, thumb_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


def ensure_thumbnail(original_path):
    """
    Ensure the thumbnail for ``original_path`` exists, generating it if needed.

    Returns the thumbnail path, or ``None`` when the original is missing or
    cannot be decoded (AVIF without plugin support, corrupt upload, etc.).
    Failures are logged and never raised: callers fall back to the original
    image so a bad file cannot block saving or serving.
    """
    if not os.path.isfile(original_path):
        return None

    thumb_path = thumbnail_path_for(original_path)
    if os.path.isfile(thumb_path) and os.path.getsize(thumb_path) > 0:
        return thumb_path

    try:
        _write_thumbnail(original_path, thumb_path)
    except (UnidentifiedImageError, Image.DecompressionBombError) as e:
        logger.warning(
            "Could not decode image for thumbnail, using original: %s (%s)",
            original_path,
            e,
        )
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Thumbnail generation failed, using original: %s (%s)",
            original_path,
            e,
            exc_info=True,
        )
        return None
    return thumb_path
