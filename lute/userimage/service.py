"""
Storage for the word card images.

Two storage modes, chosen by the ``word_image_max_edge`` setting:

* A size tier (400 / 800 / 1200): one WebP file per image, with its longest
  edge capped at the tier.  There is no original and no separate thumbnail;
  the file name carries a ``_wc`` marker (word card) so ``thumbnail_path_for``
  returns the same path.
* ``original``: the historical behaviour -- the downloaded bytes are stored
  untouched and a 400px WebP thumbnail is derived next to them (named
  ``<original>_thumb.webp``).

Images saved before this setting existed keep the derived-thumbnail naming,
so the thumbnail path is still inferred from the ``wordimages.WiSource``
file name without a database column.
"""

import io
import logging
import mimetypes
import os
import tempfile

from PIL import Image, ImageOps, UnidentifiedImageError

THUMBNAIL_MAX_EDGE = 400
THUMBNAIL_QUALITY = 80
THUMBNAIL_SUFFIX = "_thumb.webp"

# Settings key and accepted values (see lute/settings/forms.py).
IMAGE_SIZE_SETTING_KEY = "word_image_max_edge"
DEFAULT_IMAGE_MAX_EDGE = 400
IMAGE_MAX_EDGE_CHOICES = (400, 800, 1200)

# Single-file marker: these images need no derived thumbnail.
SINGLE_FILE_SUFFIX = "_wc"
WEBP_QUALITY = 82

# Formats a browser can display from a file with this extension, used when
# re-encoding would make a small image bigger than the source.
_WEB_SAFE_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}

logger = logging.getLogger(__name__)


def parse_image_max_edge(value):
    """
    The longest-edge cap for a stored setting value.

    Returns an int for a size tier, or None for ``Keep original``.
    Anything missing or unrecognised falls back to the 400px default.
    """
    if value is None:
        return DEFAULT_IMAGE_MAX_EDGE
    text = str(value).strip().lower()
    if text in ("original", "keep", "keep original", "keep_original", "keep-original"):
        return None
    try:
        edge = int(text)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_MAX_EDGE
    if edge not in IMAGE_MAX_EDGE_CHOICES:
        return DEFAULT_IMAGE_MAX_EDGE
    return edge


def current_image_max_edge():
    "The current user's image size tier, defaulting to 400."
    # Imported here to keep the service importable without an app context
    # (unit tests call the pure helpers directly).
    from lute.settings.current import current_settings

    try:
        value = current_settings().get(IMAGE_SIZE_SETTING_KEY)
    except Exception:  # pylint: disable=broad-exception-caught
        value = None
    return parse_image_max_edge(value)


def is_single_file_filename(filename):
    "True for tier-mode files, which have no separate thumbnail."
    stem = filename.rpartition(".")[0] or filename
    return stem.endswith(SINGLE_FILE_SUFFIX)


def thumbnail_filename(filename):
    "The thumbnail file name for a stored image file name."
    stem = filename.rpartition(".")[0] or filename
    return f"{stem}{THUMBNAIL_SUFFIX}"


def thumbnail_path_for(original_path):
    "The thumbnail path: the same file for tier-mode images."
    basename = os.path.basename(original_path)
    if is_single_file_filename(basename):
        return original_path
    return os.path.join(
        os.path.dirname(original_path),
        thumbnail_filename(basename),
    )


def full_url(langid, filename):
    "URL of the stored original."
    return f"/userimages/{langid}/{filename}"


def thumbnail_url(langid, filename):
    "URL of the display image; tier-mode files are their own thumbnail."
    if is_single_file_filename(filename):
        return full_url(langid, filename)
    return f"/userimages/{langid}/thumb/{filename}"


def resized_size(size, max_edge):
    """
    The image size with its longest edge capped at ``max_edge``.

    Images already within the cap keep their size (never upscaled); the
    aspect ratio is preserved and the longest edge is what gets limited,
    so a tall image is bounded by its height.
    """
    width, height = size
    if max_edge is None or max(width, height) <= max_edge:
        return (width, height)
    scale = max_edge / max(width, height)
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def _atomic_write(path, data):
    "Write bytes through a temp file so a failure leaves no partial file."
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".wc.tmp")
    try:
        with os.fdopen(fd, "wb") as out_file:
            out_file.write(data)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _save_single_file(directory, stem, data, max_edge):
    """
    Save one capped WebP, or the original bytes when they are smaller.

    Returns (filename, path).  Storing the source unchanged keeps a tiny
    JPEG from growing when the WebP re-encode would be bigger; the ``_wc``
    marker still tells the thumbnail code that this file is its own
    display image.
    """
    with Image.open(io.BytesIO(data)) as source:
        source_format = source.format
        source = ImageOps.exif_transpose(source)
        original_size = source.size
        target_size = resized_size(original_size, max_edge)

        if target_size != original_size:
            source = source.resize(target_size, Image.Resampling.LANCZOS)

        if source.mode in ("RGBA", "LA", "P"):
            source = source.convert("RGBA")
        else:
            source = source.convert("RGB")

        buffer = io.BytesIO()
        source.save(buffer, "WEBP", quality=WEBP_QUALITY, method=4)
        webp_bytes = buffer.getvalue()

    if target_size == original_size and len(webp_bytes) >= len(data):
        extension = _WEB_SAFE_EXTENSIONS.get(source_format)
        if extension is not None:
            filename = f"{stem}{SINGLE_FILE_SUFFIX}{extension}"
            path = os.path.join(directory, filename)
            _atomic_write(path, data)
            return filename, path

    filename = f"{stem}{SINGLE_FILE_SUFFIX}.webp"
    path = os.path.join(directory, filename)
    _atomic_write(path, webp_bytes)
    return filename, path


def _save_original(directory, stem, data):
    "Historical mode: untouched file plus a derived 400px thumbnail."
    filename = f"{stem}.jpeg"
    path = os.path.join(directory, filename)
    _atomic_write(path, data)
    ensure_thumbnail(path)
    return filename, path


def save_image(directory, stem, data, max_edge=DEFAULT_IMAGE_MAX_EDGE):
    """
    Save image bytes using the given size tier (``None`` = keep original).

    Returns (filename, path).  Both upload routes call this, so the three
    entry points (term form upload, Bing image save, image search manual
    upload) can never disagree about the on-disk layout.
    """
    if max_edge is None:
        return _save_original(directory, stem, data)
    try:
        return _save_single_file(directory, stem, data, max_edge)
    except (UnidentifiedImageError, Image.DecompressionBombError) as e:
        # Undecodable bytes still get stored: the browser may sniff a
        # format Pillow cannot handle (e.g. AVIF without a plugin), and a
        # bad file must not block saving a term's image.
        logger.warning(
            "Could not decode image for resizing, saving original bytes: %s (%s)",
            stem,
            e,
        )
        filename = f"{stem}{SINGLE_FILE_SUFFIX}.jpeg"
        path = os.path.join(directory, filename)
        _atomic_write(path, data)
        return filename, path


def display_mimetype(path):
    "The mimetype to serve for a stored image (thumbnails stay WebP)."
    return mimetypes.guess_type(path)[0] or "image/webp"


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
