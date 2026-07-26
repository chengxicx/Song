"""
Regression test for multiword term highlighting after save.

Bug: selecting tokens on the reading page and saving them as a
multiword term did not result in the term being highlighted on
re-render.  This was caused by context-sensitive parsers (e.g.
MeCab for Japanese) producing different tokenizations for an
isolated substring vs. the same substring in its original
sentence context.  The fix trusts the pre-tokenized ZWS-joined
text sent from the reading pane instead of re-parsing it.
"""

import pytest

from lute.db import db
from lute.models.term import Term as DBTerm
from lute.read.render.service import Service
from lute.term.model import Repository, Term
from tests.utils import make_text


def _get_textitem_statuses(paragraphs):
    """Flatten paragraphs -> list of (display_text, wo_status)."""
    out = []
    for para in paragraphs:
        for sentence in para:
            for ti in sentence:
                out.append((ti.display_text, ti.wo_status))
    return out


def test_japanese_multiword_term_gets_highlighted_after_save(japanese, app_context):
    """Selecting '行きました' and saving should highlight it on re-render."""
    zws = "\u200B"

    # Step 1: create a book with the sentence.
    text = make_text("Test book", "行きました。", japanese)
    db.session.add(text)
    db.session.commit()

    # Step 2: render the page once to discover the on-screen tokens.
    service = Service(db.session)
    paragraphs_before = service.get_paragraphs(text.text, japanese)

    page_tokens = []
    for para in paragraphs_before:
        for sentence in para:
            for ti in sentence:
                if ti.is_word:
                    page_tokens.append(ti.text)
    # MeCab should split 行きました into [行き, まし, た].
    assert page_tokens == ["行き", "まし", "た"], page_tokens

    # Step 3: simulate the JS show_multiword_term_edit_form:
    # join the selected token texts with ZWS, exactly as lute.js does.
    multiword_text = zws.join(page_tokens)
    assert multiword_text == "行き" + zws + "まし" + zws + "た"

    # Step 4: save the term via the same Repository path used by
    # /read/termform/<lid>/<text>.
    repo = Repository(db.session)
    term = repo.find_or_new(japanese.id, multiword_text)
    term.status = 1
    repo.add(term)
    repo.commit()

    # Sanity: the term was saved with ZWS preserved in both text
    # and text_lc, and token_count == 3.
    all_saved = (
        db.session.query(DBTerm)
        .filter(DBTerm.language_id == japanese.id)
        .all()
    )
    # Filter in Python so a SQL comparison quirk can't hide the match.
    saved = next(
        (t for t in all_saved if t.text == multiword_text), None
    )
    assert saved is not None, (
        f"term not saved with text={multiword_text!r}; "
        f"saved terms: {[(t.id, t.text) for t in all_saved]}"
    )
    assert saved.text == multiword_text, "text lost ZWS"
    assert saved.text_lc == multiword_text, "text_lc lost ZWS"
    assert saved.token_count == 3, f"token_count={saved.token_count}"

    # Step 5: re-render the page and verify the multiword term is
    # highlighted (i.e. a multiword TextItem with status 1 exists).
    paragraphs_after = service.get_paragraphs(text.text, japanese)
    statuses = _get_textitem_statuses(paragraphs_after)

    # The three tokens should have been collapsed into a single
    # multiword TextItem with status 1.
    multi_with_status = [
        (txt, st) for (txt, st) in statuses if zws in txt and st == 1
    ]
    assert multi_with_status, (
        f"no highlighted multiword TextItem found; got statuses={statuses}"
    )
    # And the displayed text (with ZWS stripped) should be 行きました.
    displayed = multi_with_status[0][0].replace(zws, "")
    assert displayed == "行きました", displayed


def test_japanese_multiword_term_full_http_flow(japanese, app_context, client):
    """
    End-to-end test through the actual /read/termform route, to
    verify ZWS survives URL encoding/decoding when the reading
    pane opens the term form.
    """
    zws = "\u200B"

    # Create a book with the sentence so the page exists.
    text = make_text("HTTP flow book", "行きました。", japanese)
    db.session.add(text)
    db.session.commit()

    # Simulate the JS: GET /read/termform/<lid>/<text with ZWS>.
    multiword_text = zws.join(["行き", "まし", "た"])
    url = f"/read/termform/{japanese.id}/{multiword_text}"

    # GET the form (this is what the iframe src is set to).
    resp = client.get(url)
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)

    # The form's original_text hidden field should carry the ZWS
    # intact; if it's stripped, the form would re-parse on submit.
    assert "original_text" in body, "missing original_text field"
    # The ZWS must appear in the rendered HTML value attribute.
    assert multiword_text in body, (
        f"ZWS-joined text missing from form HTML; "
        f"got body snippet: {body[:500]!r}"
    )

    # Now POST the form to actually save the term.
    # Extract the csrf token if present (TESTING disables csrf,
    # but we still need the form fields).
    post_data = {
        "language_id": str(japanese.id),
        "original_text": multiword_text,
        "text": multiword_text,
        "translation": "",
        "romanization": "",
        "status": "1",
        "sync_status": "y",
        "parentslist": "[]",
        "termtagslist": "[]",
        "current_image": "",
    }
    resp = client.post(url, data=post_data)
    assert resp.status_code == 200, resp.status_code

    # Verify the term was saved with ZWS preserved.
    saved = (
        db.session.query(DBTerm)
        .filter(DBTerm.language_id == japanese.id)
        .filter(DBTerm.token_count > 1)
        .first()
    )
    assert saved is not None, "multiword term not saved"
    assert saved.text == multiword_text, (
        f"text lost ZWS: saved={saved.text!r}, expected={multiword_text!r}"
    )
    assert saved.token_count == 3, f"token_count={saved.token_count}"

    # Finally, re-render and confirm highlighting.
    service = Service(db.session)
    paragraphs = service.get_paragraphs(text.text, japanese)
    statuses = _get_textitem_statuses(paragraphs)
    multi_with_status = [
        (txt, st) for (txt, st) in statuses if zws in txt and st == 1
    ]
    assert multi_with_status, (
        f"no highlighted multiword TextItem after HTTP save; "
        f"statuses={statuses}"
    )
