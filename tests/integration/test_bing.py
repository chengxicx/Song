"""
Test bing image search parsing.
"""

import io
import os
from unittest.mock import patch, MagicMock
import urllib.error
import urllib.request

import pytest
from PIL import Image

from lute.db import db
from lute.models.language import Language


@pytest.fixture(autouse=True)
def _language_one(app):
    "The write routes require the posted language to exist in current data."
    with app.app_context():
        if db.session.get(Language, 1) is None:
            lang = Language()
            lang.name = "English"
            db.session.add(lang)
            db.session.commit()
    yield


def test_bing_search_success_json_parsing(client):
    """
    Test successful search parsing when JSON data is present.
    """
    mock_response = MagicMock()
    html_content = """
    <html>
    <body>
        <div class="imgpt" m="{&quot;murl&quot;:&quot;https://example.com/image1.jpg&quot;,&quot;turl&quot;:&quot;https://tse.example.com/thumb1.jpg&quot;}"></div>
        <div class="imgpt" m="{&quot;murl&quot;:&quot;https://example.com/image2.jpg&quot;}"></div>
    </body>
    </html>
    """
    mock_response.read.return_value = html_content.encode("utf-8")

    with (
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("urllib.request.Request") as mock_request_cls,
    ):
        mock_urlopen.return_value.__enter__.return_value = mock_response

        resp = client.get("/bing/search/1/gato/q%3D%5BLUTE%5D")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["langid"] == 1
        assert data["text"] == "gato"
        assert len(data["images"]) == 2
        assert data["images"][0]["src"] == "https://example.com/image1.jpg"
        assert data["images"][0]["thumb"] == "https://tse.example.com/thumb1.jpg"
        assert data["images"][0]["html"] == '<img src="https://example.com/image1.jpg">'
        assert data["images"][1]["src"] == "https://example.com/image2.jpg"
        # No turl: the front end falls back to src.
        assert data["images"][1]["thumb"] == ""
        assert data["images"][1]["html"] == '<img src="https://example.com/image2.jpg">'

        mock_request_cls.assert_called_once()
        args, kwargs = mock_request_cls.call_args
        called_url = args[0]
        called_headers = kwargs.get("headers", {})

        assert "images/async" in called_url
        assert "q=gato" in called_url
        assert "User-Agent" in called_headers
        assert "Chrome/120.0.0.0" in called_headers["User-Agent"]


def test_bing_search_success_img_fallback(client):
    """
    Test successful search parsing when JSON data is not present, falling back to <img> tag parsing.
    """
    mock_response = MagicMock()
    html_content = """
    <html>
    <body>
        <img class="someclass" src="https://example.com/fallback1.jpg" />
        <img class="someclass" data-src="https://example.com/fallback2.jpg" />
    </body>
    </html>
    """
    mock_response.read.return_value = html_content.encode("utf-8")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_response

        resp = client.get("/bing/search/1/gato/q%3D%5BLUTE%5D")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["images"]) == 2
        assert data["images"][0]["src"] == "https://example.com/fallback1.jpg"
        assert data["images"][0]["thumb"] == "https://example.com/fallback1.jpg"
        assert data["images"][1]["src"] == "https://example.com/fallback2.jpg"
        assert data["images"][1]["thumb"] == "https://example.com/fallback2.jpg"


def test_bing_search_url_error(client):
    """
    Test search handles URLError.
    """
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.URLError("Connection timed out")

        resp = client.get("/bing/search/1/gato/q%3D%5BLUTE%5D")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["images"] == []
        assert "Connection timed out" in data["error_message"]


## Thumbnail generation and serving.


def _use_tmp_userimages(app, monkeypatch, tmp_path):
    "Point user image storage at tmp_path."
    path = tmp_path / "userimages"
    path.mkdir()
    monkeypatch.setattr(app.env_config.base_config, "userimagespath", str(path))
    return path


def _set_image_size(app, value):
    "Set the word card image size tier for this test."
    from lute.settings.current import current_settings

    with app.app_context():
        current_settings()["word_image_max_edge"] = value


def _image_bytes(fmt="JPEG", size=(1200, 800)):
    "Encoded test image, independent of the .jpeg file name used on disk."
    image = Image.new("RGB", size, (20, 110, 190))
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def _post_bing_save(client, content, src="https://example.com/image.jpg", langid="1"):
    "Save bytes through /bing/save, patching the download."
    from lute.bing import routes as bing_routes

    with (
        patch.object(
            bing_routes, "_resolved_addresses", return_value=["93.184.216.34"]
        ),
        patch.object(bing_routes, "_fetch_image_bytes", return_value=content),
    ):
        return client.post(
            "/bing/save",
            data={"src": src, "text": "gato", "langid": langid},
        )


def _assert_thumbnail(original_path):
    "The thumbnail exists, is WebP, and is capped at 400px."
    thumb_path = original_path.with_name(f"{original_path.stem}_thumb.webp")
    assert os.path.isfile(thumb_path)
    with Image.open(thumb_path) as thumb:
        assert thumb.format == "WEBP"
        assert max(thumb.size) <= 400
    return thumb_path


def test_bing_save_creates_webp_thumbnail(client, app, monkeypatch, tmp_path):
    "Keep original: the downloaded file plus a derived thumbnail."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _set_image_size(app, "original")
    content = _image_bytes("JPEG")
    resp = _post_bing_save(client, content)
    assert resp.status_code == 200

    data = resp.get_json()
    original_path = userimages / "1" / data["filename"]
    assert os.path.isfile(original_path)
    assert original_path.read_bytes() == content
    _assert_thumbnail(original_path)
    assert data["url"] == f"/userimages/1/{data['filename']}"
    assert data["thumb_url"] == f"/userimages/1/thumb/{data['filename']}"


def test_bing_save_handles_png_content_named_jpeg(client, app, monkeypatch, tmp_path):
    "A .jpeg file name does not stop Pillow from decoding PNG bytes."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _set_image_size(app, "original")
    resp = _post_bing_save(client, _image_bytes("PNG"))
    assert resp.status_code == 200

    data = resp.get_json()
    original_path = userimages / "1" / data["filename"]
    _assert_thumbnail(original_path)


def test_manual_upload_creates_thumbnail(client, app, monkeypatch, tmp_path):
    "Keep original: manual/paste uploads go through the same thumbnail path."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _set_image_size(app, "original")
    resp = client.post(
        "/bing/manual_image_post",
        data={
            "text": "gato",
            "langid": "1",
            "manual_image_file": (
                io.BytesIO(_image_bytes("WEBP")),
                "pasted.webp",
            ),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200

    data = resp.get_json()
    original_path = userimages / "1" / data["filename"]
    _assert_thumbnail(original_path)


def _assert_one_single_file(lang_dir, data):
    "The tier-mode response names one file, and that is all that is stored."
    files = sorted(p.name for p in lang_dir.iterdir())
    assert files == [data["filename"]]
    assert "_wc" in data["filename"]
    assert not data["filename"].endswith("_thumb.webp")
    return lang_dir / data["filename"]


@pytest.mark.parametrize(
    "tier,expected_size",
    [("400", (400, 267)), ("800", (800, 533)), ("1200", (1200, 800))],
)
def test_bing_save_stores_one_capped_webp(
    client, app, monkeypatch, tmp_path, tier, expected_size
):
    "Each size tier writes a single WebP, and the thumb URL is that file."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _set_image_size(app, tier)
    resp = _post_bing_save(client, _image_bytes("JPEG", (3000, 2000)))
    assert resp.status_code == 200

    data = resp.get_json()
    path = _assert_one_single_file(userimages / "1", data)
    with Image.open(path) as image:
        assert image.format == "WEBP"
        assert image.size == expected_size

    # Popups and the term form ask for /thumb/<filename>; it must be the
    # same stored file, not a missing derived thumbnail.
    assert data["thumb_url"] == data["url"]
    thumb_resp = client.get(f"/userimages/1/thumb/{data['filename']}")
    assert thumb_resp.status_code == 200
    assert thumb_resp.mimetype == "image/webp"
    assert thumb_resp.data == path.read_bytes()


@pytest.mark.parametrize("tier", ["400", "800", "1200"])
def test_small_image_is_not_enlarged(client, app, monkeypatch, tmp_path, tier):
    "A 300x200 source stays 300x200 and never grows on disk."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _set_image_size(app, tier)
    content = _image_bytes("JPEG", (300, 200))
    resp = _post_bing_save(client, content)
    assert resp.status_code == 200

    data = resp.get_json()
    path = _assert_one_single_file(userimages / "1", data)
    with Image.open(path) as image:
        assert image.size == (300, 200)
    assert path.stat().st_size <= len(content)


def test_manual_uploads_share_the_single_file_logic(client, app, monkeypatch, tmp_path):
    "Term form uploads and image search manual uploads hit the same logic."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    resp = client.post(
        "/bing/manual_image_post",
        data={
            "text": "gato",
            "langid": "1",
            "manual_image_file": (
                io.BytesIO(_image_bytes("JPEG", (3000, 2000))),
                "pasted.jpg",
            ),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200

    data = resp.get_json()
    path = _assert_one_single_file(userimages / "1", data)
    with Image.open(path) as image:
        assert image.format == "WEBP"
        assert image.size == (400, 267)
    assert data["thumb_url"] == data["url"]


def test_undecodable_image_saves_and_falls_back(
    client, app, monkeypatch, tmp_path, caplog
):
    "Keep original: bad bytes must not block saving; there is no thumbnail."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    _set_image_size(app, "original")
    resp = _post_bing_save(client, b"this is not an image")
    assert resp.status_code == 200

    data = resp.get_json()
    original_path = userimages / "1" / data["filename"]
    assert os.path.isfile(original_path)
    thumb_path = original_path.with_name(f"{original_path.stem}_thumb.webp")
    assert not os.path.exists(thumb_path)
    assert "Could not decode image for thumbnail" in caplog.text


def test_thumbnail_route_generates_on_first_request(client, app, monkeypatch, tmp_path):
    "Old images have no thumbnail yet: generate it on demand."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    lang_dir = userimages / "1"
    lang_dir.mkdir()
    original_path = lang_dir / "gato.jpeg"
    original_path.write_bytes(_image_bytes("JPEG"))

    resp = client.get("/userimages/1/thumb/gato.jpeg")
    assert resp.status_code == 200
    assert resp.mimetype == "image/webp"
    assert resp.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    _assert_thumbnail(original_path)


def test_thumbnail_route_falls_back_to_original_without_404(
    client, app, monkeypatch, tmp_path
):
    "If decoding fails, the thumb URL serves the original."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    lang_dir = userimages / "1"
    lang_dir.mkdir()
    original_path = lang_dir / "gato.jpeg"
    original_path.write_bytes(b"broken image bytes")

    from lute.userimage import routes as userimage_routes

    monkeypatch.setattr(userimage_routes, "ensure_thumbnail", lambda _p: None)

    resp = client.get("/userimages/1/thumb/gato.jpeg")
    assert resp.status_code == 200
    assert resp.data == b"broken image bytes"
    assert "max-age=300" in resp.headers["Cache-Control"]


def test_original_image_route_has_long_private_cache(
    client, app, monkeypatch, tmp_path
):
    "Originals are immutable by filename and can be cached aggressively."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    lang_dir = userimages / "1"
    lang_dir.mkdir()
    (lang_dir / "gato.jpeg").write_bytes(_image_bytes("JPEG"))

    resp = client.get("/userimages/1/gato.jpeg")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, max-age=31536000, immutable"


## Security: langid, remote URL fetching, and path traversal.


@pytest.mark.parametrize(
    "langid",
    [
        "../../admin/userimages/1",
        "/1",
        "abc",
        "0",
        "-1",
        "1.5",
        "999999",  # not a language in the current user's data
    ],
)
def test_bing_save_rejects_invalid_or_foreign_langid(
    client, app, monkeypatch, tmp_path, langid
):
    "A langid that is not a positive id in this user's data is a 400."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    resp = client.post(
        "/bing/save",
        data={"src": "https://example.com/x.jpg", "text": "gato", "langid": langid},
    )
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "Invalid language id"
    assert list(userimages.iterdir()) == [], "no directory may be created"


@pytest.mark.parametrize(
    "src",
    [
        "file:///etc/hosts",
        "ftp://example.com/x.jpg",
        "data:image/png;base64,AAAA",
    ],
)
def test_bing_save_rejects_non_http_urls(client, app, monkeypatch, tmp_path, src):
    "Only http/https may be fetched; other schemes are refused up front."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    resp = client.post("/bing/save", data={"src": src, "text": "gato", "langid": "1"})
    assert resp.status_code == 400
    assert "Only http and https" in resp.get_json()["reason"]
    assert list(userimages.iterdir()) == [], "no directory may be created"


@pytest.mark.parametrize(
    "src",
    [
        "http://127.0.0.1/x.jpg",
        "http://10.0.0.1/x.jpg",
        "http://172.16.0.1/x.jpg",
        "http://192.168.1.1/x.jpg",
        "http://169.254.1.1/x.jpg",
        "http://[::1]/x.jpg",
        "http://[fc00::1]/x.jpg",
    ],
)
def test_bing_save_rejects_loopback_and_private_addresses(
    client, app, monkeypatch, tmp_path, src
):
    "Internal addresses are refused so the endpoint cannot be used as an SSRF proxy."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    resp = client.post("/bing/save", data={"src": src, "text": "gato", "langid": "1"})
    assert resp.status_code == 400
    assert "private address" in resp.get_json()["reason"]
    assert list(userimages.iterdir()) == []


def test_safe_redirect_handler_rejects_private_target():
    "A redirect that lands on a private address is refused."
    from lute.bing import routes as bing_routes

    req = urllib.request.Request("https://example.com/a.jpg")
    handler = bing_routes._SafeRedirectHandler()
    with pytest.raises(bing_routes.UnsafeImageUrl):
        handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/x.jpg")


def test_safe_redirect_handler_allows_public_target(monkeypatch):
    "A redirect that stays on a public host is followed."
    from lute.bing import routes as bing_routes

    monkeypatch.setattr(
        bing_routes, "_resolved_addresses", lambda _host: ["93.184.216.34"]
    )
    req = urllib.request.Request("https://example.com/a.jpg")
    handler = bing_routes._SafeRedirectHandler()
    new_req = handler.redirect_request(
        req, None, 302, "Found", {}, "https://example.com/b.jpg"
    )
    assert new_req.full_url == "https://example.com/b.jpg"


def test_fetch_rejects_oversized_image(monkeypatch):
    "The download size cap is enforced from Content-Length."
    from lute.bing import routes as bing_routes

    class FakeResponse:
        headers = {"Content-Length": str(bing_routes._MAX_IMAGE_BYTES + 1)}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b"x" * 10

    class FakeOpener:
        def open(self, _req, timeout=None):  # pylint: disable=unused-argument
            return FakeResponse()

    monkeypatch.setattr(
        bing_routes.urllib.request, "build_opener", lambda *_handlers: FakeOpener()
    )
    with pytest.raises(bing_routes.UnsafeImageUrl):
        bing_routes._fetch_image_bytes("https://example.com/x.jpg")


def _raw_get(app, path):
    "Run a GET with the exact PATH_INFO, bypassing client-side path cleanup."
    from werkzeug.test import EnvironBuilder, run_wsgi_app

    environ = EnvironBuilder(path=path, method="GET").get_environ()
    _app_iter, status, _headers = run_wsgi_app(app, environ)
    return status


def test_thumbnail_route_rejects_path_traversal(app, monkeypatch, tmp_path):
    "Generation must never escape the requested language directory."
    root = tmp_path / "userimages"
    (root / "admin").mkdir(parents=True)
    (root / "admin" / "untouched.jpg").write_bytes(_image_bytes("JPEG"))
    (root / "1").mkdir()
    monkeypatch.setattr(app.env_config.base_config, "userimagespath", str(root))

    status = _raw_get(app, "/userimages/1/thumb/../../admin/untouched.jpg")
    assert status.startswith("404")
    assert not (root / "admin" / "untouched_thumb.webp").exists()


def test_image_route_rejects_path_traversal(app, monkeypatch, tmp_path):
    "The original-image route rejects traversal too."
    root = tmp_path / "userimages"
    (root / "admin").mkdir(parents=True)
    (root / "admin" / "untouched.jpg").write_bytes(_image_bytes("JPEG"))
    (root / "1").mkdir()
    monkeypatch.setattr(app.env_config.base_config, "userimagespath", str(root))

    status = _raw_get(app, "/userimages/1/../../admin/untouched.jpg")
    assert status.startswith("404")
    assert (root / "admin" / "untouched.jpg").exists(), "no file may be touched"


def test_missing_images_return_404(client, app, monkeypatch, tmp_path):
    "Absent files are real 404s, not 200 with an empty body."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
    (userimages / "1").mkdir()

    assert client.get("/userimages/1/thumb/missing.jpg").status_code == 404
    assert client.get("/userimages/1/missing.jpg").status_code == 404


def test_thumbnail_generation_is_concurrent_safe(app, monkeypatch, tmp_path):
    "Simultaneous first requests for the same image all succeed."
    from concurrent.futures import ThreadPoolExecutor

    from lute.userimage.service import ensure_thumbnail, thumbnail_path_for

    lang_dir = tmp_path / "userimages" / "1"
    lang_dir.mkdir(parents=True)
    original_path = lang_dir / "gato.jpeg"
    original_path.write_bytes(_image_bytes("JPEG"))
    expected_thumb = thumbnail_path_for(str(original_path))

    errors = []

    def generate(_):
        try:
            return ensure_thumbnail(str(original_path))
        except Exception as e:  # pylint: disable=broad-exception-caught
            errors.append(e)
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(generate, range(8)))

    assert errors == []
    assert all(result == expected_thumb for result in results)
    _assert_thumbnail(original_path)
