"""
Merge the fragment terms that OCR column splits left in the database.

Mokuro cuts a speech balloon's rows wherever the text ran out of room,
routinely mid-word, and the reading screen used to tokenize each row on
its own.  Every such break minted terms for the halves:

    "プレゼン" + "トです。"  ->  プレゼン, ト, です

so the database filled up with fragments -- words that are a strict
prefix of a real word the reader already has.  The rendering fix stops new
ones; this cleans up the existing ones.

A fragment is a term whose text is a strict prefix of a longer term in
the same language.  What happens to it depends on the two statuses:

  * unknown fragment, non-unknown target
        The reader knows the whole word; the fragment only ever made a
        hover card come back empty.  Drop it.
  * ignored fragment (Status.IGNORED), target known or learning
        The reader chose to ignore exactly this text, and OCR split it
        off the word they meant.  Carry the ignore over, drop the
        fragment.  When the target is *unknown* nothing is carried: the
        ignore was a verdict on the fragment's exact string, and
        imposing it on a word the reader has never seen would invent a
        decision rather than preserve one.
  * fragment at a learning status (1..5), target unknown
        Real study happened on the fragment and the target it was cut
        from is untouched, so move the status across; dropping it would
        discard the review history.
  * both unknown
        Leave both.  Merging two words nobody knows would invent a
        study history out of nothing.

Note that statuses are *not* on a single scale: Status.IGNORED is 98 and
Status.WELLKNOWN is 99, so "a higher number is further along" is false
and the rules above are spelled out per status instead.

What this never touches is a word that is not a prefix of anything --
"こ" in "ころ" is an ordinary word that merely starts with the same
characters.

Usage:
    venv/bin/python scripts/merge_manga_fragment_terms.py --dry-run
    venv/bin/python scripts/merge_manga_fragment_terms.py --apply
"""

import argparse
import glob
import json
import os
import re
import sys

from lute.app_factory import create_app
from lute.db import db
from lute.models.language import Language
from lute.models.term import Term, Status


def corpus_rows(static_folder):
    """
    Every OCR row in the installed manga books, as a set of strings.

    This is the ground truth for "is this a word?".  A fragment that the
    OCR data itself lays down as a complete row somewhere is a real
    short word -- "光" ends a row in some balloon, and begins another
    word ("光っ"), so it is not debris.  A fragment that never appears
    as a whole row only exists because a row was cut in half.
    """
    rows = set()
    pattern = os.path.join(static_folder or "", "manga", "*", "*.mokuro")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for page in data.get("pages") or []:
            for block in page.get("blocks") or []:
                for line in block.get("lines") or []:
                    for phys in re.split(r"[¶\r\n]+", line):
                        if phys.strip():
                            rows.add(phys.strip())
    return rows


LEARNING = (1, 2, 3, 4, 5)

# What the script will do, and what it did.
DROP = "drop"
CARRY = "carry"
SKIP = "skip"

# Scripts where a bare prefix is a word in its own right, so being a
# prefix proves nothing.  "3" is not debris cut out of "30", and "d" is
# not debris cut out of "dvd" -- in these scripts the split that mangles
# a word usually also mangles the text around it, so the mangled text
# does not stand alone as a valid term.  Latin fragments inside Japanese
# text ("in", "no") are handled by the character-class rule below
# instead, which keeps this from throwing away "no" / "in" / "on".
_LATIN = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def looks_like_debris(frag_text, tgt_text, frag_status):
    """
    Could `frag_text` be a piece of `tgt_text` that OCR cut mid-word?

    Being a prefix is necessary but not sufficient, so several classes
    are ruled out.  All of them are "this string is a legitimate word
    in its own right", which is exactly what a fragment is not.

    * Bare numbers.  "80" is not debris cut out of "800"; a boundary
      inside a number leaves the digits mangled, and dropping a real
      number because a longer one exists loses a word.
    * One- and two-character CJK fragments.  Japanese and Chinese words
      are routinely one or two characters ("light", "fire", "mouth"), so
      a short CJK string is far more likely to be its own word than the
      remains of a longer one -- and OCR does not reliably produce
      one-character rows to check against.  Three characters is where
      cut fragments start to dominate ("cute-i", "arigatou").
    * Short Latin fragments, unless the reader explicitly ignored them.
      "d" and "in" are ordinary words that happen to start "dvd" and
      "instead"; an ignored fragment is the reader's own verdict on the
      exact string the OCR produced, which is what this script exists
      to clean up.

    Long Latin fragments and three-or-more-character CJK fragments are
    fair game: "abcd" of "abcdefgh" is a broken word, not a word.
    """
    if not frag_text or not tgt_text:
        return False
    if any(ch.isdigit() for ch in frag_text):
        return False

    has_cjk = bool(_CJK.search(frag_text))
    if has_cjk:
        # Count the CJK characters, ignoring any Latin mixed in.
        cjk_len = len(_CJK.findall(frag_text))
        if cjk_len < 3:
            return False
    else:
        if _LATIN.search(frag_text) and len(frag_text) < 4:
            if frag_status != Status.IGNORED:
                return False
    return True


def find_fragments(session, language, corpus=None):
    """
    The fragment -> target candidates for one language.

    Only terms that could plausibly be a rendering fragment are
    considered:

    * the fragment must be a strict prefix of the target;
    * it must have no translation of its own -- a fragment the reader
      (or an import) filled in is a word in its own right;
    * it must look like debris rather than a short word (see
      looks_like_debris);
    * and when `corpus` is given, the fragment must NOT occur as a
      complete OCR row anywhere in the manga books.  This is what keeps
      "光" (which really does end a balloon row somewhere) out of the
      merge, even though "光" is a prefix of the real word "光っ".

    The shortest extension wins: that is the word the fragment was cut
    from, rather than a longer word that merely contains it.
    """
    terms = session.query(Term).filter(Term.language_id == language.id).all()
    targets = sorted(terms, key=lambda t: len(t.text_lc))

    pairs = []
    for frag in terms:
        if frag.translation:
            continue
        lc = frag.text_lc
        if corpus is not None and frag.text in corpus:
            # The OCR lays this text down as a whole row somewhere, so
            # it is a real word that merely starts like a longer one.
            continue
        for tgt in targets:
            if tgt.text_lc != lc and tgt.text_lc.startswith(lc):
                if not looks_like_debris(frag.text, tgt.text, frag.status):
                    continue
                pairs.append((frag, tgt))
                break
    return pairs


def plan(frag, tgt):
    """
    (action, target status or None, reason) for one fragment/target pair.
    """
    if frag.status == Status.UNKNOWN:
        if tgt.status == Status.UNKNOWN:
            return SKIP, None, "neither word is known"
        return DROP, None, "fragment unknown, target already has a status"
    if frag.status == Status.IGNORED:
        if tgt.status == Status.IGNORED:
            return SKIP, None, "target already ignored"
        if tgt.status == Status.UNKNOWN:
            # The fragment was ignored, the whole word is not known at
            # all.  The reader's "ignore" was a verdict on the exact
            # string the OCR produced, so carrying it over would invent
            # a decision about a word they have never seen.  Leave both.
            return SKIP, None, "target unknown: an ignore would invent a decision"
        return DROP, Status.IGNORED, "fragment ignored -> target ignored"
    if frag.status in LEARNING:
        if tgt.status == Status.UNKNOWN:
            return CARRY, frag.status, "carrying learning status %d over" % frag.status
        if tgt.status == frag.status:
            return DROP, None, "same learning status on both"
        return SKIP, None, "target already at status %d" % tgt.status
    # Well Known on the fragment: the reader marked the fragment itself
    # as known, which says nothing about the whole word.
    return SKIP, None, "fragment is Well Known, not a rendering artefact"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write (default is a dry run)",
    )
    parser.add_argument(
        "--language",
        help="only this language name (default: every language)",
    )
    parser.add_argument(
        "--no-corpus-check",
        action="store_true",
        help=(
            "skip the manga-corpus guard.  Without it, short real words "
            'that merely prefix a longer one ("light" of '
            '"lighten") can be merged away, so only use this to see '
            "what the guard is holding back."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "only act on the first N candidates (per language).  Use for "
            "a canary run before the full pass."
        ),
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        corpus = None
        if not args.no_corpus_check:
            corpus = corpus_rows(app.static_folder)
            print(
                "Corpus guard on: %d distinct OCR rows in the manga books."
                % len(corpus)
            )
            print()

        query = db.session.query(Language)
        if args.language:
            query = query.filter(Language.name == args.language)
        languages = query.all()
        if not languages:
            print("No languages matched.", file=sys.stderr)
            return 1

        counts = {DROP: 0, CARRY: 0, SKIP: 0}

        for lang in languages:
            pairs = find_fragments(db.session, lang, corpus)
            if args.limit is not None:
                pairs = pairs[: args.limit]
            if not pairs:
                continue
            print("=== %s ===" % lang.name)

            acted = 0
            for frag, tgt in sorted(pairs, key=lambda p: p[0].text_lc):
                action, new_status, reason = plan(frag, tgt)
                counts[action] += 1

                if action == DROP:
                    verb = "drop " if frag.status == Status.UNKNOWN else "carry-ignore "
                    print(
                        "  %s %-16s (of %-16s)  %s"
                        % (verb, frag.text_lc, tgt.text_lc, reason)
                    )
                    acted += 1
                    if args.apply:
                        if new_status is not None:
                            tgt.status = new_status
                        db.session.delete(frag)
                elif action == CARRY:
                    print(
                        "  carry %-16s -> %-16s  %s"
                        % (frag.text_lc, tgt.text_lc, reason)
                    )
                    acted += 1
                    if args.apply:
                        tgt.status = new_status
                        db.session.delete(frag)
                else:
                    print(
                        "  skip  %-16s (of %-16s)  %s"
                        % (frag.text_lc, tgt.text_lc, reason)
                    )

            if args.apply:
                db.session.commit()

        print()
        print(
            "%s: %d dropped, %d status-carried, %d skipped"
            % (
                "APPLIED" if args.apply else "DRY RUN",
                counts[DROP],
                counts[CARRY],
                counts[SKIP],
            )
        )
        if args.limit is not None and args.apply:
            print(
                "Canary run: capped at %d per language.  Re-run without "
                "--limit for the rest." % args.limit
            )
        elif not args.apply:
            print("Re-run with --apply to write these changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
