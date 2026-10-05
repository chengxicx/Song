"""
Guardrails for the term form's Grammar button jumping to the current sentence.

The button lives in the wordframe iframe, which knows nothing about the
reading page's text.  The parent page does -- but only if it remembers which
word the reader opened.  Three files have to agree:

  * lute-tooltip.js  -- show_term_edit_form, the single place every "open
                        this word" gesture funnels through, records the word;
  * lute-commands.js -- the panel turns that word into its sentence, picks
                        the card whose matched span covers the word, and
                        pins the rings;
  * read/index.html  -- only the term form's request asks for the jump (see
                        test_term_form_grammar_button.py).

Every one of these is a silent failure: drop the recording call and the
button simply opens the panel at the top, with no error anywhere.  So the
wiring is pinned at the source level; the selection logic itself is exercised
against the real module by test_grammar_target_js.py.
"""

import os

import pytest

_LUTE = os.path.dirname(__import__("lute").__file__)
_COMMANDS = os.path.join(_LUTE, "static", "js", "lute-commands.js")
_TOOLTIP = os.path.join(_LUTE, "static", "js", "lute-tooltip.js")


@pytest.fixture(name="commands")
def fixture_commands():
    with open(_COMMANDS, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(name="tooltip")
def fixture_tooltip():
    with open(_TOOLTIP, encoding="utf-8") as f:
        return f.read()


def _after(src, marker, length=1400):
    "A generous slice of source following `marker`; fails loudly if absent."
    assert marker in src, f"{marker!r} is gone"
    return src.split(marker, 1)[1][:length]


def test_opening_a_word_records_it_for_grammar(tooltip):
    """
    The grammar target is the word the reader opened, and show_term_edit_form
    is the only place that sees it.  It already records the same word for
    shadowing; grammar needs its own copy (shadowing consumes its own).
    """
    body = _after(tooltip, "function show_term_edit_form(el) {")
    assert "window.luteGrammarRememberWord" in body
    assert "window.luteGrammarRememberWord(el)" in body
    # Guarded like the shadowing hook: lute-tooltip.js is loaded site-wide.
    assert 'typeof window.luteGrammarRememberWord === "function"' in body


def test_the_remembered_word_is_the_same_shape_shadowing_stores(commands):
    "Only real reading-text word spans may become a grammar target."
    body = _after(commands, "window.luteGrammarRememberWord = function (el) {")
    assert 'classList.contains("word")' in body


def test_the_target_is_taken_once(commands):
    """
    Take-once, so the panel's later sub-screen refresh (which passes no
    opts) does not jump on a screen the reader has already left.
    """
    body = _after(commands, "function grammarTakeClickedWord() {", 400)
    assert "grammarClickedWord = null" in body

    open_body = _after(commands, "function open_grammar_analysis(opts) {", 400)
    assert "opts.selectTarget" in open_body
    assert "grammarTakeClickedWord()" in open_body


def test_the_panel_prefers_the_card_covering_the_clicked_word(commands):
    """
    Within the panel's own order (N5 -> N1) the card whose matched span
    covers the clicked word wins; the first card that merely shares the
    sentence is only a fallback.
    """
    body = _after(commands, "function grammarPickTarget(", 900)
    assert "grammarRunCoversWord" in body
    assert "fallback" in body
    # Sentence membership is decided on the resolved sentence element, not
    # on text, so a phrase split across nodes still counts.
    assert "ex.nodes.indexOf(targetSentence)" in body


def test_the_covering_span_test_uses_the_backend_offsets(commands):
    "grammarRunCoversWord must map the backend's character spans onto cells."
    body = _after(commands, "function grammarRunCoversWord(", 1200)
    assert "grammarRunCells(run.nodes)" in body
    assert "run.spans" in body
    assert "full.indexOf(example)" in body


def test_the_target_sentence_reuses_the_shadowing_resolver(commands):
    """
    lute-shadowing.js already resolves a word to its .textsentence,
    including joining a player-subtitle clone back to #thetext.  Duplicating
    that logic here would drift, so the resolver is preferred and the local
    copy only guards the pages that load this file without shadowing.
    """
    body = _after(commands, "function grammarTargetSentence(el) {", 900)
    assert "window.shadowingSentenceForWord" in body
    assert 'typeof window.shadowingSentenceForWord === "function"' in body


def test_the_auto_selection_is_pinned_and_scrolled_to(commands):
    """
    The jump is only useful if it sticks: the chosen card is marked, its
    rings are pinned, and a collapsed aggregate row is opened first (its
    hidden examples have no client rects, so the rings would be invisible).
    """
    # The per-card loop records what the selection needs...
    binding = _after(commands, "var boundItems = [];", 900)
    assert "boundItems.push(rec)" in binding
    assert "rec.examples.push({ runs: runs, nodes: nodes })" in commands

    # ...and the selection runs after every card is bound.
    body = _after(commands, "if (targetWordEl && targetWordEl.isConnected) {", 800)
    assert "grammarTargetSentence(targetWordEl)" in body
    assert "grammarPickTarget(boundItems, targetSentence, targetWordEl)" in body
    assert 'classList.remove("grammar-item--collapsed")' in body
    assert "setPinned(picked.runs, picked.itemEl)" in body
    assert "scrollIntoView" in body


def test_hover_out_restores_the_pin_instead_of_clearing_it(commands):
    """
    Today mouseleave always cleared the rings.  With a pinned selection the
    rings must come back after a temporary hover on another card, so both
    mouseleave handlers bind the restore, not the clear.
    """
    assert commands.count('addEventListener("mouseleave", restorePinned)') == 2
    assert 'addEventListener("mouseleave", hideAllRings)' not in commands

    set_pinned = _after(commands, "function setPinned(runs, itemEl) {", 700)
    assert 'classList.add("grammar-item--active")' in set_pinned
    assert 'classList.remove("grammar-item--active")' in set_pinned


def test_a_scroll_under_a_still_pointer_does_not_ring_another_card(commands):
    """
    The jump scrolls the panel to the selected card, and Chrome fires
    mouseenter for whatever slides under the stationary cursor -- with no
    mouseleave on the card that left.  Unguarded, the panel rings that card
    for the first frame after the click, contradicting the selection it just
    made.  A real hover always follows a mousemove, so both hover handlers are
    gated on the pointer having moved since the panel opened.
    """
    assert "function () { pointerHasMoved = true; }" in commands
    assert '"mousemove"' in commands
    assert commands.count("if (pointerHasMoved) showRings(") == 2
    assert commands.count("showRings(runs); });") == 0


def test_a_closed_panel_is_never_painted_into(commands):
    """
    The panel can be closed while the analysis request is still in flight --
    clicking a word does it, and the term form re-posts LuteTermFormOpened
    once it has rendered.  The ring layer is appended to document.body, so
    an unguarded callback paints rings for a panel that no longer exists:
    stray boxes with no panel to hover and no way to clear them.
    """
    assert "return !panel[0].isConnected;" in commands
    assert commands.count("if (panelIsGone()) return;") == 2


def test_every_card_carries_a_stable_key(commands):
    "The active card must be nameable without depending on group order."
    body = _after(commands, "function itemHtml(g, withLevelChip) {", 3000)
    assert 'data-grammar-key="' in body


def test_a_click_elsewhere_drops_the_selection_but_keeps_the_panel(commands):
    """
    The pinned selection is a "look at this" cue, not a mode: clicking
    anywhere -- the reading text, a toolbar button, another card, or the
    panel's own background -- drops the selected style and the rings.  The
    panel stays open, so hovering a card still cross-highlights afterwards.
    """
    start = commands.index("function clearPinnedSelection(ev) {")
    body = commands[start : commands.index("document.addEventListener", start)]
    assert 'classList.remove("grammar-item--active")' in body
    assert "pinnedRuns = null" in body
    assert "pinnedItemEl = null" in body
    assert "hideAllRings()" in body
    # It clears a highlight, not a view: the panel must survive the click.
    assert "closeGrammarAnalysis" not in body
    # ...except on the close button, which is already tearing it down.
    assert 'closest(".grammar-analysis-panel__close")' in body
    # Capture phase, so the clear lands before whatever the click was for.
    assert 'document.addEventListener("click", clearPinnedSelection, true)' in commands


def test_the_click_listener_does_not_outlive_the_panel(commands):
    """
    clearPinnedSelection is a closure over one panel's state, so a listener
    left behind holds the whole detached panel -- and a re-open would stack
    another one on top.  The element is the only handle on the closure, so
    the unhook rides on the element and must run before remove() drops it.
    """
    assert "panel[0].luteGrammarCleanup = function () {" in commands
    assert (
        'document.removeEventListener("click", clearPinnedSelection, true)' in commands
    )
    close_body = _after(commands, "function closeGrammarAnalysis() {", 900)
    assert "stale.luteGrammarCleanup()" in close_body
    assert close_body.index("luteGrammarCleanup") < close_body.index(".remove()")
