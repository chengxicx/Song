"""
Test bing image search parsing.
"""

import io
import os
from unittest.mock import patch, MagicMock
import urllib.error
import urllib.request

from PIL import Image


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


def _image_bytes(fmt="JPEG", size=(1200, 800)):
    "Encoded test image, independent of the .jpeg file name used on disk."
    image = Image.new("RGB", size, (20, 110, 190))
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def _post_bing_save(client, content):
    "Save bytes through /bing/save, patching the download."
    mock_response = MagicMock()
    mock_response.read.return_value = content
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value = mock_response
        return client.post(
            "/bing/save",
            data={
                "src": "https://example.com/image.jpg",
                "text": "gato",
                "langid": "1",
            },
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
    "Saving a Bing image leaves the original alone and derives a thumbnail."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
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
    resp = _post_bing_save(client, _image_bytes("PNG"))
    assert resp.status_code == 200

    data = resp.get_json()
    original_path = userimages / "1" / data["filename"]
    _assert_thumbnail(original_path)


def test_manual_upload_creates_thumbnail(client, app, monkeypatch, tmp_path):
    "Manual/paste uploads go through the same thumbnail path."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
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


def test_undecodable_image_saves_and_falls_back(
    client, app, monkeypatch, tmp_path, caplog
):
    "Bad bytes must not block saving; there is just no thumbnail."
    userimages = _use_tmp_userimages(app, monkeypatch, tmp_path)
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
