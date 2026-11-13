"""
Getting and saving bing image search results.
"""

import os
import datetime
import hashlib
import ipaddress
import re
import socket
import urllib.request
import json
from urllib.parse import urlsplit
from flask import (
    Blueprint,
    request,
    Response,
    render_template,
    jsonify,
    current_app,
    url_for,
)
from lute.db import db
from lute.models.language import Language
from lute.userimage.service import (
    current_image_max_edge,
    full_url,
    save_image,
    thumbnail_url,
)

bp = Blueprint("bing", __name__, url_prefix="/bing")

_IMAGE_TIMEOUT_SECONDS = 10
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_IMAGE_SCHEMES = ("http", "https")
_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bing.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class UnsafeImageUrl(Exception):
    "The requested image URL is not allowed."


def _resolved_addresses(hostname):
    "All IP addresses the host name resolves to."
    return [info[4][0] for info in socket.getaddrinfo(hostname, None)]


def _validate_image_url(url):
    """
    Raise UnsafeImageUrl unless url is an http(s) resource on a public host.

    Only http/https are allowed, so file:// and other schemes are refused
    before any connection is attempted.  Every resolved address must be
    public: loopback, private, link-local and reserved ranges (including
    IPv6 ::1 and fc00::/7) are rejected to stop server-side request
    forgery.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_IMAGE_SCHEMES:
        raise UnsafeImageUrl("Only http and https image URLs are allowed")
    if not parts.hostname:
        raise UnsafeImageUrl("Image URL has no host")

    try:
        addresses = _resolved_addresses(parts.hostname)
    except OSError as e:
        raise UnsafeImageUrl(f"Could not resolve image host: {e}") from e
    if not addresses:
        raise UnsafeImageUrl("Could not resolve image host")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as e:
            raise UnsafeImageUrl("Image host resolved to an invalid address") from e
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeImageUrl(
                f"Refusing to fetch an image from a private address ({address})"
            )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    "Validate every redirect target before following it."

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_image_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_image_bytes(src):
    "Download the image into memory, enforcing the size cap."
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    req = urllib.request.Request(src, headers=_IMAGE_HEADERS)
    with opener.open(req, timeout=_IMAGE_TIMEOUT_SECONDS) as response:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_IMAGE_BYTES:
                    raise UnsafeImageUrl("Image is too large")
            except ValueError:
                pass
        data = response.read(_MAX_IMAGE_BYTES + 1)
    if len(data) > _MAX_IMAGE_BYTES:
        raise UnsafeImageUrl("Image is too large")
    return data


def _validated_langid(raw):
    """
    Return the posted language id, or None when it is not acceptable.

    The value must be a plain positive integer, and the language must
    exist in the current user's data (the single-user instance's data
    when multi-user mode is off).  Anything else -- path fragments,
    absolute paths, non-numbers, another user's language -- is refused.
    """
    raw = (raw or "").strip()
    if not raw.isdigit():
        return None
    langid = int(raw)
    if langid <= 0:
        return None
    if db.session.get(Language, langid) is None:
        return None
    return langid


@bp.route(
    "/search_page/<int:langid>/<string:text>/<string:searchstring>", methods=["GET"]
)
def bing_search_page(langid, text, searchstring):
    """
    Load initial empty search page, passing real URL for subsequent ajax call to get images.

    Sometimes Bing image searches block or fail, so providing the initial empty search page
    lets the user know work is in progress.  The user can therefore interact with the page
    immediately. The template for this route then makes an ajax call to the "bing_search()"
    method below which actually does the search.
    """

    # Create URL for bing_search and pass into template.
    search_url = url_for(
        "bing.bing_search", langid=langid, text=text, searchstring=searchstring
    )

    return render_template(
        "imagesearch/index.html", langid=langid, text=text, search_url=search_url
    )


@bp.route("/search/<int:langid>/<string:text>/<string:searchstring>", methods=["GET"])
def bing_search(langid, text, searchstring):
    "Do an image search."

    # pylint: disable=too-many-locals
    # Searching for images slows acceptance tests.  If NO_BING_IMAGES
    # environment setting, don't do a search.
    if "NO_BING_IMAGES" in os.environ:
        return render_template(
            "imagesearch/index.html", langid=langid, text=text, images=[]
        )

    search = urllib.parse.quote(text)
    params = searchstring.replace("[LUTE]", search)
    params = params.replace("###", search)  # TODO remove_old_###_placeholder: remove

    url = "https://www.bing.com/images/async?" + params + "&first=1&count=35&mmasync=1"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    content = ""
    error_msg = ""
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as s:
            content = s.read().decode("utf-8")
    except urllib.error.URLError as e:
        content = ""
        error_msg = str(e.reason)
    except Exception as e:  # pylint: disable=broad-exception-caught
        content = ""
        error_msg = str(e)

    # Sample data returned by bing image search:
    # <img class="mimg vimgld" ... data-src="https:// ...">
    # or
    # <img class="mimg rms_img" ... src="https://tse4.mm.bing ..." >
    images = []
    for m_data in re.findall(r' m="({.*?})"', content):
        try:
            data = json.loads(m_data.replace("&quot;", '"'))
            murl = data.get("murl", "")
            turl = data.get("turl", "")
            if murl and murl.startswith("http"):
                if not (turl and turl.startswith("http")):
                    turl = ""
                images.append(
                    {
                        # The picker displays the Bing thumbnail; saving
                        # still downloads the full murl.
                        "html": '<img src="' + murl + '">',
                        "src": murl,
                        "thumb": turl,
                    }
                )
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    if not images:

        def is_search_img(img):
            if 'src="/' in img:
                return False
            normalized = img.replace("data-src=", "src=")
            return bool(re.search(r'src="https?://', normalized))

        def build_struct(image):
            src = "missing"
            normalized_source = image.replace("data-src=", "src=")
            m = re.search(r'src="(.*?)"', normalized_source)
            if m:
                src = m.group(1)
            return {"html": image, "src": src, "thumb": src}

        raw_images = list(re.findall(r"(<img .*?>)", content, re.I))
        images = [build_struct(i) for i in raw_images if is_search_img(i)]

    # Reduce image load count so we don't kill subpage loading.
    # Also bing seems to throttle images if the count is higher (??).
    images = images[:25]

    ret = {
        "langid": langid,
        "text": text,
        "images": images,
        "error_message": error_msg,
    }
    return jsonify(ret)


def _get_dir_and_stem(langid, text):
    "Make a directory if needed, return [dir, file name stem]"
    # env_config is the user-scoped proxy (multi-user isolation).
    root = os.path.realpath(current_app.env_config.userimagespath)
    image_dir = os.path.join(root, str(langid))
    # The id is already a validated positive integer, but check the
    # resolved path too: no write may land outside the userimages root.
    if os.path.commonpath([os.path.realpath(image_dir), root]) != root:
        raise UnsafeImageUrl("Image directory is outside the user images folder")
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S%f")[:-3]
    hash_part = hashlib.md5(text.encode()).hexdigest()[:8]
    return [image_dir, f"{timestamp}_{hash_part}"]


def _save_response(langid, filename):
    "The JSON shared by both save routes."
    return {
        "url": full_url(langid, filename),
        "thumb_url": thumbnail_url(langid, filename),
        "filename": filename,
    }


def _bad_langid_response():
    "A readable 400 for an invalid or foreign language id."
    return jsonify({"reason": "Invalid language id"}), 400


@bp.route("/save", methods=["POST"])
def bing_save():
    """
    Save the posted image data to DATAPATH/userimages,
    returning the filename.
    """
    src = request.form["src"]
    text = request.form["text"]
    langid = _validated_langid(request.form.get("langid", ""))
    if langid is None:
        return _bad_langid_response()

    try:
        _validate_image_url(src)
        content = _fetch_image_bytes(src)
    except UnsafeImageUrl as e:
        return jsonify({"reason": str(e)}), 400
    except urllib.error.URLError as e:
        return jsonify({"reason": f"Could not download image: {e.reason}"}), 502
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Some sites won't allow direct calls :-( not sure what to do.
        return jsonify({"reason": f"Could not download image: {e}"}), 502

    try:
        imgdir, stem = _get_dir_and_stem(langid, text)
        filename, _path = save_image(imgdir, stem, content, current_image_max_edge())
    except UnsafeImageUrl as e:
        return jsonify({"reason": str(e)}), 400
    except OSError as e:
        return jsonify({"reason": f"Could not save image: {e}"}), 500

    return jsonify(_save_response(langid, filename))


@bp.route("/manual_image_post", methods=["POST"])
def manual_image_post():
    """
    For manual posts of images (not bing image clicks).
    Save the posted image data to DATAPATH/userimages,
    returning the filename.
    """
    text = request.form["text"]
    langid = _validated_langid(request.form.get("langid", ""))
    if langid is None:
        return _bad_langid_response()

    if "manual_image_file" not in request.files:
        return Response("No file part in request", status=400)

    f = request.files["manual_image_file"]
    if f.filename == "":
        return Response("No selected file", status=400)

    try:
        imgdir, stem = _get_dir_and_stem(langid, text)
        filename, _path = save_image(imgdir, stem, f.read(), current_image_max_edge())
    except UnsafeImageUrl as e:
        return jsonify({"reason": str(e)}), 400
    except OSError as e:
        return jsonify({"reason": f"Could not save image: {e}"}), 500

    return jsonify(_save_response(langid, filename))
