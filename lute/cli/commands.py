"""
Simple CLI commands.
"""

import json

import click
from flask import Blueprint

from lute.db import db
from lute.cli.language_term_export import generate_language_file, generate_book_file
from lute.cli.import_books import import_books_from_csv
from lute.cli.term_parent_backfill import (
    format_report,
    plan_as_dict,
    run_backfill,
    undo_backfill,
)
from lute.review import enqueue as review_enqueue

bp = Blueprint("cli", __name__)


@bp.cli.command("hello")
def hello():
    "Say hello -- proof-of-concept CLI command only."
    msg = """
    Hello there!

    This is the Lute cli.

    There may be some experimental scripts here ...
    nothing that will change or damage your Lute data,
    but the CLI may change.

    Thanks for looking.
    """
    print(msg)


@bp.cli.command("language_export")
@click.argument("language")
@click.argument("output_path")
def language_export(language, output_path):
    """
    Get all terms from all books in the language, and write a
    data file of term frequencies and children.
    """
    generate_language_file(language, output_path)


@bp.cli.command("book_term_export")
@click.argument("bookid")
@click.argument("output_path")
def book_term_export(bookid, output_path):
    """
    Get all terms for the given book, and write a
    data file of term frequencies and children.
    """
    generate_book_file(bookid, output_path)


@bp.cli.command("import_books_from_csv")
@click.option(
    "--commit",
    is_flag=True,
    help="""
    Commit the changes to the database. If not set, import in dry-run mode. A
    list of changes will be printed out but not applied.
""",
)
@click.option(
    "--tags",
    default="",
    help="""
    A comma-separated list of tags to apply to all books.
""",
)
@click.option(
    "--language",
    default="",
    help="""
    The name of the default language to apply to each book, as it appears in
    your language settings. If unset, the language must be indicated in the
    "language" column of the CSV file.
""",
)
@click.argument("file")
def import_books_from_csv_cmd(language, file, tags, commit):
    """
    Import books from a CSV file.

    The CSV file must have a header row with the following, case-sensitive,
    column names. The order of the columns does not matter. The CSV file may
    include additional columns, which will be ignored.

      - title: the title of the book

      - text: the text of the book

      - language: [optional] the name of the language of book, as it appears in
      your language settings. If unspecified, the language specified on the
      command line (using the --language option) will be used.

      - url: [optional] the source URL for the book

      - tags: [optional] a comma-separated list of tags to apply to the book
      (e.g., "audiobook,beginner")

      - audio: [optional] the path to the audio file of the book. This should
      either be an absolute path, or a path relative to the CSV file.

      - bookmarks: [optional] a semicolon-separated list of audio bookmark
      positions, in seconds (decimals permitted; e.g., "12.34;42.89;89.00").
    """
    tags = list(tags.split(",")) if tags else []
    import_books_from_csv(file, language, tags, commit)


@bp.cli.command("parent_backfill")
@click.option(
    "--language",
    required=True,
    help="Name of the language to process, e.g. Japanese.",
)
@click.option(
    "--commit",
    is_flag=True,
    help="""
    Write the changes. If not set, report only: nothing is written.
""",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="""
    Only process the first N links (by term id), for a canary run.
""",
)
@click.option(
    "--new-parent-status",
    type=click.Choice(["inherit", "zero"]),
    default="inherit",
    help="""
    Status to give a parent term that has to be created: "inherit" takes
    the status of the child that points at it, "zero" creates it unknown.
""",
)
@click.option(
    "--multi-token-policy",
    type=click.Choice(["skip", "single-content-word"]),
    default="skip",
    help="""
    How to treat terms made of several tokens (text with zero-width
    spaces).  "skip" ignores all of them; "single-content-word" also links
    the ones that hold exactly one content word (入りました -> 入る) while
    still refusing concatenations (似ている -> 似るいる).
""",
)
@click.option(
    "--audit",
    default=None,
    help="""
    Path for the JSONL audit log of the links created (used by
    parent_backfill_undo). Only written with --commit.
""",
)
@click.option(
    "--json",
    "json_path",
    default=None,
    help="Write the full plan as JSON to this path, for diffing runs.",
)
def parent_backfill_cmd(
    language, commit, limit, new_parent_status, multi_token_policy, audit, json_path
):
    """
    Link terms to their dictionary form (lemma) parent.

    For every term that has a dictionary form different from its own text,
    add the parent link.  Already-linked terms are never touched, the new
    links never sync statuses (WoSyncStatus stays 0), and no existing
    term's status is changed.
    """
    result = run_backfill(
        language,
        commit=commit,
        limit=limit,
        new_parent_status=new_parent_status,
        audit_path=audit,
        multi_token_policy=multi_token_policy,
    )
    print(format_report(result))
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(plan_as_dict(result), f, ensure_ascii=False, indent=1)
        print("\nPlan written to %s" % json_path)


@bp.cli.command("parent_backfill_undo")
@click.option("--audit", required=True, help="The audit log to undo.")
@click.option(
    "--commit",
    is_flag=True,
    help="Actually do it. If not set, report only.",
)
@click.option(
    "--delete-created-terms",
    is_flag=True,
    help="""
    Also delete the parent terms the run created, when nothing else
    references them.
""",
)
def parent_backfill_undo_cmd(audit, commit, delete_created_terms):
    """
    Remove the links added by a parent_backfill --commit run.

    Uses its audit log.  The links are always removed; the parent terms
    the run created are only removed with --delete-created-terms, and only
    when they have no other links, tags, images or translation.
    """
    removed, terms_removed, terms_kept = undo_backfill(
        audit, commit=commit, delete_created_terms=delete_created_terms
    )
    mode = "Removed" if commit else "Would remove"
    print("%s %d link(s)." % (mode, removed))
    if commit and delete_created_terms:
        print(
            "Parent terms: %d deleted, %d kept (still referenced)."
            % (terms_removed, terms_kept)
        )


@bp.cli.command("review_sync")
def review_sync_cmd():
    """
    Enqueue review cards for all learning terms.

    Syncing only ever adds: cards already in the queue keep their
    scheduling, and no card is ever removed.
    """
    counts = review_enqueue.auto_admit(db.session)
    total = sum(counts.values())
    by_type = ", ".join(f"{k}: {v}" for k, v in counts.items())
    print(f"Added {total} review card(s) ({by_type or 'none new'}).")
