/**
 * Review session runner + review index page actions.
 *
 * A session prefetches all due cards in one /review/start call; each
 * grade is a small /review/grade post.  Recognition cards are graded
 * with the four FSRS buttons; recall/cloze cards can be typed and
 * are auto-graded (Again on a wrong answer), or revealed and
 * self-graded.
 *
 * The card is one fixed shell with three regions (question, answer,
 * grading) so revealing never makes the buttons jump: the shell is
 * built once per card and only its contents change.
 *
 * Keyboard: Space/Enter reveals, 1-4 grade, Enter checks a typed
 * answer, Space/Enter moves on after a typed check.
 */
window.LuteReview = (function () {
  "use strict";

  const state = {
    cards: [],
    idx: 0,
    // "question" | "answer" | "next" -- what the keyboard should do.
    mode: "question",
    current: null,
    // {card_id, card_type, term_text, rating} of what an undo would
    // reverse, or null when there is nothing to undo.
    undo: null,
  };

  const RATING_LABELS = { 1: "Again", 2: "Hard", 3: "Good", 4: "Easy" };

  const PROMPTS = {
    recognition: "Recall the meaning",
    recall: "Type the word",
    cloze: "Fill in the blank",
  };

  const CARD_TYPE_LABELS = {
    recognition: "Recognition",
    recall: "Recall",
    cloze: "Cloze",
  };

  async function post_json(url, data) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
    const payload = await resp.json();
    if (!resp.ok) {
      throw payload;
    }
    return payload;
  }

  function el(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function show_error(err) {
    const box = el("review_session_error");
    const card = el("review_card");
    if (box) {
      box.style.display = "block";
      box.innerHTML = `<p>${esc(err.error || err)}</p>
        <p><a href="/review/index">Back to review index</a></p>`;
    }
    if (card) {
      card.style.display = "none";
    }
  }

  /* ---------- index page actions ---------- */

  async function sync() {
    const msg = el("review_sync_message");
    if (!msg) return;
    msg.textContent = "Syncing ...";
    try {
      const result = await post_json("/review/sync");
      const parts = [`Added ${result.cards_added} cards.`];
      if (result.skipped_no_sentence > 0) {
        parts.push(
          `Skipped ${result.skipped_no_sentence} cloze cards (no sentence yet).`
        );
      }
      result.specs.forEach((s) => {
        parts.push(`${s.name}: matched ${s.terms_matched}.`);
      });
      Object.entries(result.errors || {}).forEach(([name, err]) => {
        parts.push(`ERROR in ${name}: ${err}`);
      });
      msg.textContent = parts.join(" ");
      if (result.cards_added > 0) {
        window.location.reload();
      }
    } catch (err) {
      const errors = err && err.errors ? Object.values(err.errors) : [err];
      msg.textContent = `Sync failed: ${errors.join("; ")}`;
    }
  }

  async function install_fsrs() {
    const msg = el("review_install_message");
    if (msg) msg.textContent = "Installing the fsrs package ...";
    try {
      const result = await post_json("/review/scheduler/install");
      if (msg) msg.textContent = result.message;
      if (result.ok) {
        setTimeout(() => window.location.reload(), 1500);
      }
    } catch (err) {
      if (msg) msg.textContent = `Install failed: ${err.error || err}`;
    }
  }

  /* ---------- session ---------- */

  async function start_session() {
    try {
      const payload = await post_json("/review/start");
      state.cards = payload.cards;
      state.idx = 0;
      update_undo(payload.undo);
      if (state.cards.length === 0) {
        el("review_progress").innerHTML = "";
        el("review_card").innerHTML =
          '<p class="rv-done">Nothing due.  <a href="/review/index">Back to review index</a></p>';
        return;
      }
      show_current();
    } catch (err) {
      show_error(err);
    }
  }

  /* ---------- undo ---------- */

  function update_undo(info) {
    state.undo = info || null;
    const btn = el("review_undo");
    if (!btn) return;
    if (!state.undo) {
      btn.hidden = true;
      return;
    }
    btn.hidden = false;
    const label = RATING_LABELS[state.undo.rating] || "";
    const term = state.undo.term_text || "";
    btn.title = `Reverse the last grade (${term}${label ? ", " + label : ""})`;
  }

  async function undo() {
    if (!state.undo) return;
    const btn = el("review_undo");
    if (btn) btn.disabled = true;
    try {
      const res = await post_json("/review/undo");
      update_undo(res.undo);
      // The cards were fetched before any grading, so the undone card
      // is already on screen exactly as it was.
      const i = state.cards.findIndex((c) => c.id === res.card_id);
      if (i >= 0) {
        state.idx = i;
        show_current();
      } else {
        // Graded in an earlier session: nothing on screen to go back to.
        window.location.reload();
      }
    } catch (err) {
      if (btn) btn.disabled = false;
      const msg = el("review_sync_message") || el("review_progress");
      if (msg) msg.textContent = `Undo failed: ${err.error || err}`;
    }
  }

  function render_progress() {
    const box = el("review_progress");
    if (!box) return;
    const total = state.cards.length;
    const n = state.idx + 1;
    const pct = Math.round((state.idx / total) * 100);
    box.innerHTML = `
      <div class="rv-progress-track"><div class="rv-progress-fill" style="width: ${pct}%"></div></div>
      <span class="rv-progress-text">${n} / ${total}</span>
    `;
  }

  function card_shell(c) {
    const badge = CARD_TYPE_LABELS[c.card_type] || c.card_type;
    const prompt = PROMPTS[c.card_type] || "";
    const typing = c.card_type === "recognition" ? "" : `<input type="text" id="review_typing" class="rv-typing" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="your answer">`;
    // Recognition has nothing to check, so it gets a single action.
    const check =
      c.card_type === "recognition"
        ? ""
        : '<button class="btn btn-primary" id="review_check">Check</button>';
    return `
      <div class="rv-card">
        <div class="rv-card-head">
          <span class="rv-badge">${esc(badge)}</span>
          <span class="rv-prompt">${esc(prompt)}</span>
        </div>
        <div class="rv-question" id="review_question"></div>
        ${typing}
        <div class="rv-actions">
          ${check}
          <button class="btn btn-secondary" id="review_reveal">Show answer
            <kbd>Space</kbd></button>
        </div>
        <div class="rv-answer" id="review_answer" hidden></div>
        <div class="rv-grades" id="review_grades" hidden></div>
      </div>
    `;
  }

  function question_html(c) {
    if (c.card_type === "recognition") {
      return `<div class="rv-term">${esc(c.term_text)}</div>`;
    }
    if (c.card_type === "recall") {
      return `<div class="rv-translation-front">${esc(c.translation)}</div>`;
    }
    // cloze: server-rendered sentence with the term blanked out.
    return `<div class="rv-sentence rv-sentence-front">${c.sentence_blank}</div>`;
  }

  function show_current() {
    const c = state.cards[state.idx];
    state.current = c;
    state.mode = "question";
    render_progress();
    const box = el("review_card");
    box.innerHTML = card_shell(c);
    el("review_question").innerHTML = question_html(c);

    const reveal_btn = el("review_reveal");
    reveal_btn.addEventListener("click", () => reveal(c));
    const check_btn = el("review_check");
    if (check_btn) check_btn.addEventListener("click", () => check_typed(c));
    const typing = el("review_typing");
    if (typing) {
      typing.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          check_typed(c);
        }
      });
      typing.focus();
    }
  }

  function back_html(c, banner) {
    const pieces = [];
    if (banner) {
      pieces.push(
        `<p class="rv-banner ${banner.ok ? "rv-ok" : "rv-bad"}">${esc(banner.text)}</p>`
      );
    }
    // The term is the answer for recall/cloze, but a recognition card
    // already has it on the front -- don't print it twice.
    if (c.card_type !== "recognition") {
      pieces.push(`<div class="rv-term rv-answer-term">${esc(c.term_text)}</div>`);
    }
    if (c.romanization) {
      pieces.push(`<p class="rv-reading">${esc(c.romanization)}</p>`);
    }
    if (c.sentence) {
      pieces.push(`<p class="rv-sentence">${c.sentence}</p>`);
    }
    if (c.translation) {
      pieces.push(`<p class="rv-translation">${esc(c.translation)}</p>`);
    }
    if (c.image) {
      pieces.push(`<img class="rv-image" src="${esc(c.image)}" alt="">`);
    }
    return pieces.join("\n");
  }

  function grade_buttons_html(c) {
    const ratings = [
      [1, "Again"],
      [2, "Hard"],
      [3, "Good"],
      [4, "Easy"],
    ];
    return ratings
      .map(([rating, label]) => {
        const interval = c.intervals ? ` (${c.intervals[rating - 1]})` : "";
        return `<button class="rv-grade rv-grade-${rating}" data-rating="${rating}">
                  <span class="rv-grade-key">${rating}</span>
                  <span class="rv-grade-label">${label}${esc(interval)}</span>
                </button>`;
      })
      .join("\n");
  }

  function show_grades(c) {
    const grades = el("review_grades");
    grades.innerHTML = grade_buttons_html(c);
    grades.hidden = false;
    grades.querySelectorAll(".rv-grade").forEach((b) => {
      b.addEventListener("click", () => {
        b.disabled = true;
        grade(c, parseInt(b.dataset.rating, 10));
      });
    });
    state.mode = "answer";
  }

  function reveal(c) {
    const check = el("review_check");
    const reveal_btn = el("review_reveal");
    if (check) check.style.display = "none";
    if (reveal_btn) reveal_btn.style.display = "none";
    const answer = el("review_answer");
    answer.innerHTML = back_html(c, null);
    answer.hidden = false;
    show_grades(c);
  }

  async function check_typed(c) {
    const typing = el("review_typing");
    const typed = typing ? typing.value : "";
    if (typed.trim() === "") {
      reveal(c);
      return;
    }
    // Typed check: Good if correct, Again if wrong.  The server is
    // the judge; the response tells us what actually happened.
    try {
      const result = await post_json("/review/grade", {
        card_id: c.id,
        rating: 3,
        typed,
      });
      c.answer = result.answer;
      update_undo(result.undo);
      const check = el("review_check");
      const reveal_btn = el("review_reveal");
      if (check) check.style.display = "none";
      if (reveal_btn) reveal_btn.style.display = "none";
      if (typing) typing.disabled = true;
      const answer = el("review_answer");
      answer.innerHTML = back_html(
        c,
        result.correct
          ? { ok: true, text: "Correct" }
          : { ok: false, text: `Not quite -- the answer is ${result.answer}` }
      );
      answer.hidden = false;
      const grades = el("review_grades");
      const next_label = result.correct ? "Next" : "Next (marked Again)";
      grades.innerHTML = `<button class="btn btn-primary" id="review_next">${next_label} <kbd>Space</kbd></button>`;
      grades.hidden = false;
      el("review_next").addEventListener("click", () => advance());
      state.mode = "next";
      const next = el("review_next");
      if (next) next.focus();
    } catch (err) {
      show_error(err);
    }
  }

  async function grade(c, rating) {
    try {
      const res = await post_json("/review/grade", {
        card_id: c.id,
        rating,
        typed: null,
      });
      update_undo(res.undo);
      advance();
    } catch (err) {
      show_error(err);
    }
  }

  function advance() {
    state.idx += 1;
    if (state.idx >= state.cards.length) {
      el("review_progress").innerHTML = "";
      el("review_card").innerHTML =
        '<p class="rv-done">Session done.  <a href="/review/index">Back to review index</a></p>';
      return;
    }
    show_current();
  }

  /* ---------- keyboard ---------- */

  function on_keydown(e) {
    // Undo comes first: it is the one shortcut that uses a modifier.
    if ((e.metaKey || e.ctrlKey) && (e.key === "z" || e.key === "Z")) {
      if (state.undo) {
        e.preventDefault();
        undo();
      }
      return;
    }

    const c = state.current;
    if (!c || el("review_card").style.display === "none") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    const in_input = (e.target.tagName || "").toLowerCase() === "input";

    if (state.mode === "answer") {
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= 4) {
        const btn = document.querySelector(`.rv-grade[data-rating="${n}"]`);
        if (btn) {
          e.preventDefault();
          btn.click();
        }
      }
      return;
    }

    if (state.mode === "next") {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        advance();
      }
      return;
    }

    // mode === "question"
    if (in_input) return; // Enter is handled by the input itself
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      reveal(c);
    }
  }

  function init() {
    const btn = el("review_undo");
    if (btn) btn.addEventListener("click", () => undo());
  }

  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("keydown", on_keydown);

  return {
    sync,
    install_fsrs,
    start_session,
    undo,
  };
})();
