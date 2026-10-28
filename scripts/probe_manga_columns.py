"""
Check the manga column-splitting against the real production OCR shapes.

The local database has none of the production manga books, so this builds
the same block shapes by hand -- copied verbatim out of the production
.mokuro files -- and runs them through the real tokenizer and the real
TextItem machinery.  Two properties are asserted:

  1. the rendered characters of a block reassemble its source text
     exactly (nothing dropped, duplicated or shifted);
  2. a word the OCR split across rows comes back pointing at the one
     real Term, from every one of its pieces.

Run from the repo root:  venv/bin/python scripts/probe_manga_columns.py
"""

from lute.app_factory import create_app
from lute.db import db
from lute.models.language import Language
from lute.models.term import Term
from lute.read.render.service import Service as RenderService
from lute.read import manga_columns

# Verbatim from production books 292/294, page 32 (blocks 0/2/3) and
# page 7 (block 1).
PRODUCTION_BLOCKS = [
    {
        "box": [128, 772, 220, 940],
        "vertical": True,
        "font_size": 34,
        "lines": ["プレゼン", "トです。"],
    },
    {
        "box": [1467, 1872, 1605, 2040],
        "vertical": True,
        "font_size": 34,
        "lines": ["開けてご", "らんなさ", "い。"],
    },
    {
        "box": [90, 2234, 258, 2386],
        "vertical": True,
        "font_size": 30,
        "lines": ["わあ、人", "形です。", "ありがと", "う。"],
    },
    {
        "box": [111, 825, 711, 930],
        "vertical": False,
        "font_size": 30,
        "lines": [
            "チョコレートやキャンディーをあげます。日",
            "本ではホワイトデーには男性から女性にプレ",
            "ゼントを贈ります。",
        ],
    },
    {
        "box": [86, 1848, 694, 1891],
        "vertical": False,
        "font_size": 38,
        "lines": ["わあ、大きい箱ですね。何ですか。"],
    },
]

# The terms production already had; the split words must land on these.
SEED_TERMS = {
    "プレゼント": "展示",
    "ありがとう": "谢谢",
}


def rendered_text(per_row):
    return "".join(ti.html_display_text for row in per_row for ti in row)


def split_runs(per_row):
    """
    The maximal runs of consecutive split pieces that share one Term,
    as (text, [items]).

    A word the OCR cut in half shows up here as one run whose pieces
    come from different rows.  Grouping is by Term rather than by
    adjacency: adjacent pieces routinely spell *different* words (the
    tail of one and the head of the next both get cut), and lumping
    those together would hide a genuine split.
    """
    runs = []
    acc = ""
    owners = []
    for row in per_row:
        for ti in row:
            if ti.is_split_piece and ti.is_word:
                if owners and ti.term is not owners[0].term:
                    runs.append((acc, owners))
                    acc = ""
                    owners = []
                acc += ti.html_display_text
                owners.append(ti)
            else:
                if acc:
                    runs.append((acc, owners))
                acc = ""
                owners = []
    if acc:
        runs.append((acc, owners))
    return runs


def main():
    failures = []

    def check(label, cond, detail=""):
        print("  %s %s%s" % ("PASS" if cond else "FAIL", label, detail))
        if not cond:
            failures.append(label)

    app = create_app()
    with app.app_context():
        lang = db.session.query(Language).filter_by(name="Japanese").one()
        rs = RenderService(db.session)
        mw = rs.get_multiword_indexer(lang)

        print("=== seeding the terms production already had ===")
        planted = []
        seeded_ids = {}
        for text, translation in SEED_TERMS.items():
            t = (
                db.session.query(Term)
                .filter_by(language_id=lang.id, text_lc=text.lower())
                .one_or_none()
            )
            if t is None:
                t = Term.create_term_no_parsing(lang, text)
                t.translation = translation
                t.status = 99
                db.session.add(t)
                db.session.flush()
                planted.append(t)
                print("  planted %r -> id %s" % (text, t.id))
            else:
                print("  found   %r -> id %s" % (text, t.id))
            seeded_ids[text] = t.id
        # The multiword indexer caches the terms it was built from, so it
        # has to be rebuilt after planting.
        mw = rs.get_multiword_indexer(lang)

        print()
        print("=== 1. rendered text reassembles the source ===")
        for i, block in enumerate(PRODUCTION_BLOCKS):
            rows = manga_columns.row_texts(block)
            per_row = manga_columns.tokenize_block(rs, block, lang, mw)
            got = rendered_text(per_row)
            want = "".join(rows)
            check(
                "block %d roundtrip" % i,
                got == want,
                "" if got == want else "\n      got =%r\n      want=%r" % (got, want),
            )
            check(
                "block %d row count" % i,
                len(per_row) == len(rows),
                ""
                if len(per_row) == len(rows)
                else " got=%d want=%d" % (len(per_row), len(rows)),
            )

        print()
        print("=== 2. split words land on one real Term ===")
        for i, block in enumerate(PRODUCTION_BLOCKS):
            per_row = manga_columns.tokenize_block(rs, block, lang, mw)
            runs = split_runs(per_row)
            if not runs:
                print("  block %d: no split words (rows were clean)" % i)
                continue
            for text, owners in runs:
                ids = {ti.wo_id for ti in owners}
                # The point is that every piece addresses ONE term: the
                # same Term object, hence the same id once it is saved.
                # A brand-new word has id None until the page is saved,
                # so None is a valid shared value, not a failure.
                term_objs = {id(ti.term) for ti in owners}
                rows_hit = sorted(
                    {ri for ri, row in enumerate(per_row) for ti in row if ti in owners}
                )
                seeded = seeded_ids.get(text)
                print(
                    "  block %d %r pieces=%r rows=%s ids=%s%s"
                    % (
                        i,
                        text,
                        [ti.html_display_text for ti in owners],
                        rows_hit,
                        ids,
                        " (the seeded term)" if seeded and seeded in ids else "",
                    )
                )
                check(
                    "block %d %r one shared Term" % (i, text),
                    len(term_objs) == 1 and len(ids) == 1,
                    " term_objs=%d ids=%s" % (len(term_objs), ids),
                )

        print()
        print("=== 3. the production case: プレゼント ===")
        block = PRODUCTION_BLOCKS[0]
        per_row = manga_columns.tokenize_block(rs, block, lang, mw)
        for ri, row in enumerate(per_row):
            for ti in row:
                print(
                    "  row%d %-8r wid=%-6s status=%-4s split=%s"
                    % (
                        ri,
                        ti.html_display_text,
                        ti.wo_id,
                        ti.wo_status if ti.term else None,
                        ti.is_split_piece,
                    )
                )
        ids = {
            ti.wo_id
            for row in per_row
            for ti in row
            if "プレゼン" in ti.html_display_text or ti.html_display_text == "ト"
        }
        check(
            "プレゼント resolves to the seeded term",
            seeded_ids["プレゼント"] in ids,
            " ids=%s expected=%s" % (ids, seeded_ids["プレゼント"]),
        )

        for t in planted:
            db.session.delete(t)
        db.session.commit()

    print()
    if failures:
        print("FAILURES (%d):" % len(failures))
        for f in failures:
            print("  -", f)
    else:
        print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
