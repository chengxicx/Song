"""
Import LingQ Mini Stories txt files as books, grouped into series.

Each "== N. ... ==" block in a txt file becomes one book (Lute's normal
page-splitting and parsing pipeline runs via BookService.import_book).
All books from one language share a series tag (e.g. "LingQ日语小故事");
series in this Song fork are book tags listed in the UserSetting
'book_series_tags', so the tags are appended there after import.

Additive only: existing books are never touched, and books whose title
already exists for the language are skipped, so the script is safe to
re-run (idempotent).

Usage (dry run by default):

    python -m scripts.import_ministories /path/to/ministories_ja.txt ...
    python -m scripts.import_ministories --apply --user <user> /tmp/lingq/*.txt
    python -m scripts.import_ministories --apply --user <user> --limit 1 /tmp/lingq/*.txt
"""

import argparse
import contextlib
import pathlib
import re
import sys

from lute.app_factory import create_app
from lute.book.model import Book
from lute.book.service import Service as BookService
from lute.db import db
from lute.models.repositories import BookRepository, LanguageRepository
from lute.models.repositories import UserSettingRepository
from lute.multiuser import context as mu_context
from lute.multiuser import store as mu_store

SERIES_SETTING_KEY = "book_series_tags"

# txt filename stem -> (Lute language name, series tag for that language).
LANG_MAP = {
    "ja": ("Japanese", "LingQ日语小故事"),
    "ko": ("Korean", "LingQ韩语小故事"),
    "ru": ("Russian", "LingQ俄语小故事"),
    "yue": ("Cantonese Chinese", "LingQ粤语小故事"),
    "en": ("English", "LingQ英语小故事"),
    "fr": ("French", "LingQ法语小故事"),
    "es": ("Spanish", "LingQ西班牙语小故事"),
    "th": ("Thai", "LingQ泰语小故事"),
}

_HEADER_RE = re.compile(r"^==\s+(.+?)\s+==$")
_COLLECTION_RE = re.compile(r"^#\s*collection:\s*(\S+)")


def parse_file(path):
    """
    Parse one ministories txt file.

    Returns (collection_url, [(title, text), ...]).  Blocks are split on
    '== title ==' header lines; '----------' separators become '---'
    page-break markers that Lute's page splitter understands.
    """
    collection_url = None
    blocks = []
    title = None
    lines_out = []

    def _flush():
        nonlocal title, lines_out
        if title is not None:
            text = "\n".join(lines_out).strip()
            if text:
                blocks.append((title.strip(), text))
        title = None
        lines_out = []

    with open(path, mode="r", encoding="utf-8") as f:
        for raw in f.read().splitlines():
            m = _HEADER_RE.match(raw)
            if m:
                _flush()
                title = m.group(1)
                continue
            m = _COLLECTION_RE.match(raw)
            if m:
                collection_url = m.group(1)
                continue
            if title is None:
                # File-level comments and blanks before the first block.
                continue
            if raw.strip() and set(raw.strip()) == {"-"} and len(raw.strip()) >= 3:
                # '----------' separators -> Lute page-break marker.
                lines_out.append("---")
            else:
                lines_out.append(raw)
    _flush()
    return collection_url, blocks


def import_file(path, apply, limit):
    "Import one txt file.  Returns (created, skipped) counts."
    stem = path.stem.replace("ministories_", "")
    if stem not in LANG_MAP:
        print(f"  ! unknown language key '{stem}' in {path.name}, skipping")
        return (0, 0)
    lang_name, series_tag = LANG_MAP[stem]

    lang = LanguageRepository(db.session).find_by_name(lang_name)
    if lang is None:
        print(f"  ! language '{lang_name}' not found in db, skipping {path.name}")
        return (0, 0)

    collection_url, blocks = parse_file(path)
    print(f"=== {path.name}: {lang_name}, {len(blocks)} blocks, tag '{series_tag}' ===")

    book_repo = BookRepository(db.session)
    service = BookService()
    created = skipped = 0
    for title, text in blocks:
        if limit is not None and created >= limit:
            print(f"  -- limit {limit} reached, remaining blocks skipped")
            break
        if book_repo.find_by_title(title, lang.id) is not None:
            skipped += 1
            continue
        if not apply:
            print(f"  [dry-run] would create: {title} ({len(text)} chars)")
            created += 1
            continue
        bo = Book()
        bo.language_name = lang_name
        bo.title = title
        bo.text = text
        bo.source_uri = collection_url
        bo.book_tags = [series_tag]
        dbbook = service.import_book(bo, db.session)
        print(
            f"  + created BkID={dbbook.id} pages={len(dbbook.texts)}: {title}"
        )
        created += 1
    return (created, skipped)


def update_series_tags(series_tags):
    "Append the new tags to the book_series_tags user setting, preserving order."
    if not series_tags:
        return
    usrepo = UserSettingRepository(db.session)
    current = usrepo.get_dynamic_value(SERIES_SETTING_KEY) or ""
    tags = [t.strip() for t in current.split(",") if t.strip()]
    added = [t for t in series_tags if t not in tags]
    if not added:
        print("Series tags already configured; settings unchanged.")
        return
    tags.extend(added)
    usrepo.set_dynamic_value(SERIES_SETTING_KEY, ",".join(tags))
    db.session.commit()
    print(f"Series tags added to {SERIES_SETTING_KEY}: {', '.join(added)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("txtfiles", nargs="+", help="ministories_*.txt files")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually create books (default is a dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="create at most N books per file (canary run)",
    )
    parser.add_argument(
        "--user",
        help=(
            "multi-user mode: run as this user (their own database is "
            "read and, with --apply, written).  Required for --apply "
            "when multi-user mode is on."
        ),
    )
    args = parser.parse_args()

    app = create_app()

    if not args.user and mu_store.enabled():
        print(
            "Multi-user mode is on: all reads/writes hit one user's own "
            "database, so --user <name> is required.",
            file=sys.stderr,
        )
        return 1

    scope = mu_context.user_scope(args.user) if args.user else contextlib.nullcontext()

    with app.app_context(), scope:
        total_created = total_skipped = 0
        series_tags = set()
        for p in args.txtfiles:
            path = pathlib.Path(p)
            created, skipped = import_file(path, args.apply, args.limit)
            total_created += created
            total_skipped += skipped
            stem = path.stem.replace("ministories_", "")
            if stem in LANG_MAP:
                series_tags.add(LANG_MAP[stem][1])

        if args.apply:
            update_series_tags(series_tags)

        print()
        print(
            f"Done: created={total_created}, skipped(existing)={total_skipped}, "
            f"mode={'APPLY' if args.apply else 'DRY-RUN'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
