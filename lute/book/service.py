"""
book helper routines.
"""

import json
import os
import re
import shutil
import uuid
import zipfile
from io import StringIO, TextIOWrapper, BytesIO
from datetime import datetime
from dataclasses import dataclass
from tempfile import TemporaryFile
import requests
from bs4 import BeautifulSoup
from flask import current_app, flash
from openepub import Epub, EpubError
from pypdf import PdfReader
from subtitle_parser import SrtParser
from lute.book.model import Repository


class BookImportException(Exception):
    """
    Exception to throw on book import error.
    """

    def __init__(self, message="A custom error occurred", cause=None):
        self.cause = cause
        self.message = message
        super().__init__(message)


@dataclass
class BookDataFromUrl:
    "Data class"
    title: str = None
    source_uri: str = None
    text: str = None


def youtube_video_id(url):
    """
    Extract the YouTube video id from a URL, or return None.

    Handles watch, youtu.be, embed, and shorts URLs.
    """
    if not url:
        return None
    patterns = [
        r"youtube\.com/watch\?[^#\s]*v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"youtube\.com/live/([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def parse_subtitle_file(
    filename, filestream, language=None, resplit_sentences=False
):
    """
    Parse an srt/vtt subtitle file.

    Returns (text, cues_json), where text is the subtitle texts joined
    by newlines, and cues_json is a JSON string of
    [{"start": secs, "end": secs, "text": str}, ...].

    When language is Japanese and resplit_sentences is True, the cues
    are also refined: mid-sentence breaks are merged and over-long cues
    are split so each cue roughly corresponds to one sentence (see
    lute.book.japanese_srt).  resplit_sentences defaults to False.
    """
    _, ext = os.path.splitext(filename)
    ext = (ext or "").lower()

    fte = FileTextExtraction()
    content = fte._get_text_stream_content(filestream, "utf-8-sig")

    return parse_subtitle_content(
        content,
        language=language,
        resplit_sentences=resplit_sentences,
        ext=ext,
    )


def parse_subtitle_content(content, language=None, resplit_sentences=False, ext=".srt"):
    """
    Parse srt/vtt subtitle content (a string).

    Returns (text, cues_json), where text is the subtitle texts joined
    by newlines, and cues_json is a JSON string of cue dicts.
    """
    cues = _parse_cues(content, ext)
    if resplit_sentences and _is_japanese(language):
        from lute.book.japanese_srt import refine_japanese_cues  # pylint: disable=import-outside-toplevel

        cues = refine_japanese_cues(cues, language)
    text = "\n".join(c["text"] for c in cues)
    return text, json.dumps(cues, ensure_ascii=False)


def _parse_cues(content, ext):
    "Parse srt/vtt content into a list of cue dicts."
    if ext == ".vtt":
        return _parse_vtt_cues(content)
    return _parse_srt_cues(content)


def _parse_srt_cues(content):
    "Parse SRT content into a list of cue dicts."
    parser = SrtParser(StringIO(content))
    parser.parse()
    return [
        {"start": s.start / 1000.0, "end": s.end / 1000.0, "text": s.text}
        for s in parser.subtitles
    ]


def _timecode_to_secs(tc):
    """
    Convert an SRT/VTT timestamp (HH:MM:SS.mmm or HH:MM:SS,mmm) to seconds.
    """
    tc = tc.strip().replace(",", ".")
    parts = tc.split(":")
    if len(parts) == 2:
        parts = ["0"] + parts
    if len(parts) != 3:
        raise BookImportException(f"Invalid timestamp {tc!r}")
    try:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    except ValueError as e:
        raise BookImportException(f"Invalid timestamp {tc!r}") from e
    return hours * 3600 + minutes * 60 + seconds


def _parse_vtt_cues(content):
    """
    Parse WebVTT content into a list of cue dicts.

    This is a small hand-rolled parser because the subtitle_parser
    WebVttParser chokes on real-world (YouTube) VTT exports:
      - cue timing lines carry trailing settings, e.g.
        "00:00:04.200 --> 00:00:07.000 align:start position:0%",
      - metadata lines like "X-TIMESTAMP-MAP=..." appear between the
        WEBVTT header and the first cue,
      - cue identifiers are not always numeric.
    """
    lines = content.split("\n")
    cues = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if "-->" not in line:
            # Skip header / metadata / NOTE / STYLE lines until a cue.
            i += 1
            continue
        m = re.search(r"(\S+)\s+-->\s+(\S+)", line)
        if not m:
            i += 1
            continue
        start = _timecode_to_secs(m.group(1))
        end = _timecode_to_secs(m.group(2))
        i += 1
        text_lines = []
        while i < n:
            cur = lines[i].strip()
            if cur == "" or "-->" in cur:
                break
            text_lines.append(cur)
            i += 1
        text = "\n".join(text_lines)
        if text:
            cues.append({"start": start, "end": end, "text": text})
        while i < n and lines[i].strip() == "":
            i += 1
    return cues


def _format_srt_timestamp(secs):
    "Format seconds as HH:MM:SS,mmm for SRT output."
    total_ms = max(0, int(round(float(secs or 0) * 1000)))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    seconds = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def cues_to_srt_text(cues):
    """
    Convert a list of cues ({start, end, text}) to SRT-formatted text
    with timestamps, so it can be edited directly.
    """
    lines = []
    for i, c in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(
            f"{_format_srt_timestamp(c.get('start', 0))} --> "
            f"{_format_srt_timestamp(c.get('end', 0))}"
        )
        lines.append(c.get("text") or "")
        lines.append("")
    return "\n".join(lines).rstrip()


def _is_japanese(language):
    """True if the given language (a lute.models.Language) is Japanese."""
    if language is None:
        return False
    name = (getattr(language, "name", "") or "").strip().lower()
    ptype = (getattr(language, "parser_type", "") or "").strip().lower()
    return name == "japanese" or ptype in ("japanese", "japanese_sudachi")


class FileTextExtraction:
    "Utility to extract text from various file formats."

    def get_file_content(self, filename, filestream):
        """
        Get the content of the file.
        """
        _, ext = os.path.splitext(filename)
        ext = (ext or "").lower()

        messages = {
            ".pdf": """
            Note: pdf imports can be inaccurate, due to how PDFs are encoded.
            Please be aware of this while reading.
            """
        }
        msg = messages.get(ext)
        if msg is not None:
            flash(msg, "notice")

        handlers = {
            ".txt": self._get_textfile_content,
            ".epub": self._get_epub_content,
            ".pdf": self._get_pdf_content,
            ".srt": self._get_srt_content,
            ".vtt": self._get_vtt_content,
        }
        handler = handlers.get(ext)
        if handler is None:
            raise ValueError(f'Unknown file extension "{ext}"')
        content = handler(filename, filestream).strip()
        if content == "":
            raise BookImportException(f"{filename} is empty.")
        return content

    def _get_text_stream_content(self, fstream, encoding="utf-8"):
        "Gets content from simple text stream."

        usestream = fstream
        # May have to convert the fstream to a a BytesIO stream.
        # GitHub CI caught this, and per ChatGPT: In Python 3.10,
        # SpooledTemporaryFile no longer automatically gains all
        # file-like methods when rolled over to a regular temporary
        # file. Specifically, it seems that the object lacks the
        # readable method required by TextIOWrapper to validate the
        # stream ...
        #
        # I haven't looked into this deeply, but when running Python
        # 3.10.16 on my mac, "inv accept -k bad_text_files" failed on
        # line "with TextIOWrapper(fstream, encoding=encoding) as
        # decoded:" with "AttributeError: 'SpooledTemporaryFile'
        # object has no attribute 'readable'. Did you mean:
        # 'readline'?"..  Converting usestream to BytesIO fixed it.
        if not hasattr(fstream, "readable"):
            usestream = BytesIO(fstream.read())  # Wrap in BytesIO if needed
        with TextIOWrapper(usestream, encoding=encoding) as decoded:
            return decoded.read()

    def _get_textfile_content(self, filename, filestream):
        "Get content as a single string."
        try:
            return self._get_text_stream_content(filestream)
        except UnicodeDecodeError as e:
            f = filename
            msg = f"{f} is not utf-8 encoding, please convert it to utf-8 first (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e

    def _get_epub_content(self, filename, filestream):
        """
        Get the content of the epub as a single string.
        """
        content = ""
        try:
            if hasattr(filestream, "seekable"):
                epub = Epub(stream=filestream)
                content = epub.get_text()
            else:
                # We get a SpooledTemporaryFile from the form but this doesn't
                # implement all file-like methods until python 3.11. So we need
                # to rewrite it into a TemporaryFile
                with TemporaryFile() as tf:
                    filestream.seek(0)
                    tf.write(filestream.read())
                    epub = Epub(stream=tf)
                    content = epub.get_text()
        except EpubError as e:
            msg = f"Could not parse {filename} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e
        return content

    def _get_pdf_content(self, filename, filestream):
        "Get content as a single string from a PDF file using PyPDF2."
        content = ""
        try:
            pdf_reader = PdfReader(filestream)
            for page in pdf_reader.pages:
                content += page.extract_text()
            return content
        except Exception as e:
            msg = f"Could not parse {filename} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e

    def _get_srt_content(self, filename, filestream):
        """
        Get the content of the srt as a single string.
        """
        try:
            srt_content = self._get_text_stream_content(filestream, "utf-8-sig")
            content, _ = parse_subtitle_content(srt_content, ext=".srt")
            return content
        except Exception as e:
            msg = f"Could not parse {filename} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e

    def _get_vtt_content(self, filename, filestream):
        """
        Get the content of the vtt as a single string.
        """
        try:
            vtt_content = self._get_text_stream_content(filestream, "utf-8-sig")
            content, _ = parse_subtitle_content(vtt_content, ext=".vtt")
            return content
        except Exception as e:
            msg = f"Could not parse {filename} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e


class Service:
    "Service."

    def _unique_fname(self, filename):
        """
        Return secure name pre-pended with datetime string.
        """
        current_datetime = datetime.now()
        formatted_datetime = current_datetime.strftime("%Y%m%d_%H%M%S")
        _, ext = os.path.splitext(filename)
        ext = (ext or "").lower()
        newfilename = uuid.uuid4().hex
        return f"{formatted_datetime}_{newfilename}{ext}"

    def extract_manga(self, filename, filestream):
        """
        Extract a Mokuro manga archive (zip/cbz) into static/manga/{uuid}/.

        Returns (manga_path, mokuro_dict), where manga_path is the
        relative directory under the static folder (e.g.
        "manga/<uuid>"), and mokuro_dict is the parsed .mokuro JSON.

        Raises BookImportException on invalid archives.
        """
        _, ext = os.path.splitext(filename)
        ext = (ext or "").lower()
        if ext not in (".zip", ".cbz"):
            raise BookImportException(
                f"Unsupported manga file extension '{ext}'; use .zip or .cbz."
            )

        manga_root = os.path.join(current_app.static_folder, "manga")
        manga_uuid = uuid.uuid4().hex
        target_dir = os.path.join(manga_root, manga_uuid)
        os.makedirs(target_dir, exist_ok=True)

        mokuro_name = None
        try:
            with zipfile.ZipFile(BytesIO(filestream.read())) as zf:
                names = zf.namelist()
                mokuro_candidates = [
                    n for n in names if n.lower().endswith(".mokuro")
                ]
                if not mokuro_candidates:
                    raise BookImportException(
                        "Archive contains no .mokuro file; "
                        "ensure it is a Mokuro manga archive."
                    )
                mokuro_name = mokuro_candidates[0]
                # Extract members safely: reject names that would escape
                # the target directory (zip-slip protection).
                for member in names:
                    target = os.path.join(target_dir, member)
                    if not os.path.realpath(target).startswith(
                        os.path.realpath(target_dir) + os.sep
                    ):
                        shutil.rmtree(target_dir, ignore_errors=True)
                        raise BookImportException(
                            f"Archive member '{member}' would escape the "
                            "extraction directory; aborting import."
                        )
                zf.extractall(target_dir)
        except zipfile.BadZipFile as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            msg = f"Could not unzip {filename} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e

        # Parse the .mokuro JSON and store it on the book.
        mokuro_path = os.path.join(target_dir, mokuro_name)
        try:
            with open(mokuro_path, "r", encoding="utf-8") as mf:
                mokuro = json.load(mf)
        except (ValueError, OSError) as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            msg = f"Could not parse mokuro file {mokuro_name} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e

        # Fix up img_path entries so they point to actual extracted files.
        # Mokuro archives may have the .mokuro JSON at the zip root but
        # images inside a volume/ subdirectory (e.g. volume "Foo" with
        # img_path "001.jpg" means the file is "Foo/001.jpg").  We also
        # support the case where the img_path already contains the right
        # relative path, or where the files live directly next to the
        # .mokuro file.
        mokuro_dir = os.path.dirname(mokuro_path) or target_dir

        def _find_rel_image(rel_candidate):
            "Return True if rel_candidate resolves to an existing file under target_dir."
            abs_path = os.path.normpath(os.path.join(target_dir, rel_candidate))
            if not os.path.realpath(abs_path).startswith(
                os.path.realpath(target_dir) + os.sep
            ):
                return False
            return os.path.isfile(abs_path)

        # Build a lookup: basename -> list of (rel_path_from_target_dir)
        # for every regular file extracted under target_dir.
        basename_index = {}
        for root, _dirs, files in os.walk(target_dir):
            for f in files:
                abs_f = os.path.join(root, f)
                try:
                    rel_f = os.path.relpath(abs_f, target_dir).replace("\\", "/")
                except ValueError:
                    continue
                basename_index.setdefault(os.path.basename(f).lower(), []).append(rel_f)

        volume = (mokuro.get("volume") or "").strip().replace("\\", "/")

        for page in mokuro.get("pages") or []:
            raw = (page.get("img_path") or "").replace("\\", "/")
            if not raw:
                continue

            candidates = []
            # 1) As written, relative to the extracted root.
            candidates.append(raw)
            # 2) Relative to the .mokuro file's directory.
            mokuro_rel = os.path.relpath(
                os.path.normpath(os.path.join(mokuro_dir, raw)), target_dir
            ).replace("\\", "/")
            candidates.append(mokuro_rel)
            # 3) Under the volume/ subdirectory (mokuro default layout).
            if volume:
                candidates.append(f"{volume.rstrip('/')}/{raw.lstrip('/')}")
                # Volume directory next to the .mokuro file.
                vol_abs = os.path.normpath(os.path.join(mokuro_dir, volume, raw))
                try:
                    candidates.append(
                        os.path.relpath(vol_abs, target_dir).replace("\\", "/")
                    )
                except ValueError:
                    pass
            # 4) Just the basename in any subdirectory (fallback scan).
            base = os.path.basename(raw)
            matches = basename_index.get(base.lower()) or []
            # Prefer matches whose path contains the volume name if any.
            if volume:
                sorted_matches = sorted(
                    matches,
                    key=lambda p: (0 if volume.lower() in p.lower() else 1, len(p)),
                )
            else:
                sorted_matches = sorted(matches, key=len)
            candidates.extend(sorted_matches)

            resolved = None
            seen = set()
            for cand in candidates:
                cand = cand.replace("\\", "/").lstrip("./")
                if cand in seen:
                    continue
                seen.add(cand)
                if _find_rel_image(cand):
                    resolved = cand
                    break

            if resolved is not None:
                page["img_path"] = resolved

        return f"manga/{manga_uuid}", mokuro

    def youtube_title(self, url):
        """
        Best-effort title lookup for a YouTube video.

        Uses the YouTube oEmbed endpoint (no API key required), and
        falls back to a title based on the video id.
        """
        vid = youtube_video_id(url)
        fallback = f"YouTube video ({vid})" if vid else "YouTube video"
        if vid is None:
            return fallback
        try:
            oembed = f"https://www.youtube.com/oembed?url={url}&format=json"
            response = requests.get(oembed, timeout=10)
            response.raise_for_status()
            data = response.json()
            title = data.get("title", "").strip()
            if title:
                return title[:200]
        except (requests.exceptions.RequestException, ValueError):
            pass
        return fallback

    def save_audio_file(self, audio_file_field_data):
        """
        Save the file to disk, return its filename.
        """
        filename = self._unique_fname(audio_file_field_data.filename)
        fp = os.path.join(current_app.env_config.useraudiopath, filename)
        audio_file_field_data.save(fp)
        return filename

    def book_data_from_url(self, url):
        """
        Parse the url and load source data for a new Book.
        This returns a domain object, as the book is still unparsed.
        """
        s = None
        try:
            timeout = 20  # seconds
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            s = response.content
        except requests.exceptions.RequestException as e:
            msg = f"Could not parse {url} (error: {str(e)})"
            raise BookImportException(message=msg, cause=e) from e

        soup = BeautifulSoup(s, "html.parser")
        extracted_text = []

        # Add elements in order found.
        for element in soup.descendants:
            if element.name in ("h1", "h2", "h3", "h4", "p"):
                extracted_text.append(element.text)

        title_node = soup.find("title")
        orig_title = title_node.string if title_node else url

        short_title = orig_title[:150]
        if len(orig_title) > 150:
            short_title += " ..."

        b = BookDataFromUrl()
        b.title = short_title
        b.source_uri = url
        b.text = "\n\n".join(extracted_text)
        return b

    def import_book(self, book, session):
        """
        Save the book as a dbbook, parsing and saving files as needed.
        Returns new book created.
        """

        def _raise_if_file_missing(p, fldname):
            if not os.path.exists(p):
                raise BookImportException(f"Missing file {p} given in {fldname}")

        def _raise_if_none(p, fldname):
            if p is None:
                raise BookImportException(f"Must set {fldname}")

        fte = FileTextExtraction()
        if book.text_source_path:
            _raise_if_file_missing(book.text_source_path, "text_source_path")
            tsp = book.text_source_path
            with open(tsp, mode="rb") as stream:
                book.text = fte.get_file_content(tsp, stream)

        if book.text_stream:
            _raise_if_none(book.text_stream_filename, "text_stream_filename")
            book.text = fte.get_file_content(
                book.text_stream_filename, book.text_stream
            )

        if book.audio_source_path:
            _raise_if_file_missing(book.audio_source_path, "audio_source_path")
            newname = self._unique_fname(book.audio_source_path)
            fp = os.path.join(current_app.env_config.useraudiopath, newname)
            shutil.copy(book.audio_source_path, fp)
            book.audio_filename = newname

        if book.audio_stream:
            _raise_if_none(book.audio_stream_filename, "audio_stream_filename")
            newname = self._unique_fname(book.audio_stream_filename)
            fp = os.path.join(current_app.env_config.useraudiopath, newname)
            with open(fp, mode="wb") as fcopy:  # Use "wb" to write in binary mode
                while chunk := book.audio_stream.read(
                    8192
                ):  # Read the stream in chunks (e.g., 8 KB)
                    fcopy.write(chunk)
            book.audio_filename = newname

        if book.manga_stream:
            _raise_if_none(book.manga_stream_filename, "manga_stream_filename")
            manga_path, mokuro = self.extract_manga(
                book.manga_stream_filename, book.manga_stream
            )
            book.manga_path = manga_path
            book.manga_data = json.dumps(mokuro, ensure_ascii=False)

        repo = Repository(session)
        dbbook = repo.add(book)
        repo.commit()
        return dbbook
