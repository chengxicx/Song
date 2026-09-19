/**
 * Review session runner + review index page actions.
 *
 * A session prefetches all due cards in one /review/start call; each
 * grade is a small /review/grade post.  Recognition cards are graded
 * with the four FSRS buttons; recall/cloze cards can be typed and
 * are auto-graded (Again on a wrong answer), or revealed and
 * self-graded.
 */
window.LuteReview = (function () {
  "use strict";

  const state = {
    cards: [],
    idx: 0,
    revealed: false,
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
      if (state.cards.length === 0) {
        el("review_card").innerHTML =
          '<p>Nothing due.  <a href="/review/index">Back</a></p>';
        el("review_progress").textContent = "";
        return;
      }
      show_current();
    } catch (err) {
      show_error(err);
    }
  }

  function show_current() {
    state.revealed = false;
    const c = state.cards[state.idx];
    el("review_progress").textContent = `Card ${state.idx + 1} of ${state.cards.length}`;
    const box = el("review_card");
    let front = "";
    let input = "";

    if (c.card_type === "recognition") {
      front = `<div class="review-front-term">${esc(c.term_text)}</div>`;
    } else if (c.card_type === "recall") {
      front = `<div class="review-front-translation">${esc(c.translation)}</div>`;
      input = `<input type="text" id="review_typing" class="review-typing"
                 placeholder="type the word" autocomplete="off">`;
    } else if (c.card_type === "cloze") {
      front = `<div class="review-front-sentence">${c.sentence_blank}</div>`;
      input = `<input type="text" id="review_typing" class="review-typing"
                 placeholder="type the missing word (optional)" autocomplete="off">`;
    }

    box.innerHTML = `
      ${front}
      ${input}
      <div class="review-actions">
        <button class="btn btn-primary" id="review_check">Check</button>
        <button class="btn btn-secondary" id="review_reveal">Show answer</button>
      </div>
      <div id="review_back" style="display: none;"></div>
      <div id="review_grades" style="display: none;"></div>
    `;

    el("review_check").addEventListener("click", () => check_typed(c));
    el("review_reveal").addEventListener("click", () => reveal(c));
    const typing = el("review_typing");
    if (typing) {
      typing.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          check_typed(c);
        }
      });
    }
  }

  function back_html(c, banner) {
    const pieces = [];
    if (banner) pieces.push(`<p class="review-banner ${banner.ok ? "review-ok" : "review-bad"}">${banner.text}</p>`);
    pieces.push(`<div class="review-back-term">${esc(c.term_text)}</div>`);
    if (c.romanization) pieces.push(`<p class="review-reading">${esc(c.romanization)}</p>`);
    if (c.sentence) pieces.push(`<p class="review-sentence">${c.sentence}</p>`);
    if (c.translation) pieces.push(`<p class="review-translation">${esc(c.translation)}</p>`);
    if (c.image) pieces.push(`<img class="review-image" src="${esc(c.image)}">`);
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
        return `<button class="btn btn-secondary review-grade" data-rating="${rating}">
                  ${label}${esc(interval)}
                </button>`;
      })
      .join("\n");
  }

  function wire_grade_buttons(c) {
    document.querySelectorAll(".review-grade").forEach((b) => {
      b.addEventListener("click", async () => {
        b.disabled = true;
        await grade(c, parseInt(b.dataset.rating, 10), null);
      });
    });
  }

  function reveal(c) {
    state.revealed = true;
    el("review_check").style.display = "none";
    el("review_reveal").style.display = "none";
    el("review_back").innerHTML = back_html(c, null);
    el("review_back").style.display = "block";
    const grades = el("review_grades");
    grades.innerHTML = grade_buttons_html(c);
    grades.style.display = "block";
    wire_grade_buttons(c);
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
      el("review_check").style.display = "none";
      el("review_reveal").style.display = "none";
      el("review_back").innerHTML = back_html(
        c,
        result.correct
          ? { ok: true, text: "Correct!" }
          : { ok: false, text: `Not quite.  Answer: ${result.answer}` }
      );
      el("review_back").style.display = "block";
      const grades = el("review_grades");
      const next_label = result.correct ? "Next" : "Next (marked Again)";
      grades.innerHTML = `<button class="btn btn-primary" id="review_next">${next_label}</button>`;
      grades.style.display = "block";
      el("review_next").addEventListener("click", () => advance());
    } catch (err) {
      show_error(err);
    }
  }

  async function grade(c, rating, typed) {
    try {
      await post_json("/review/grade", { card_id: c.id, rating, typed });
      advance();
    } catch (err) {
      show_error(err);
    }
  }

  function advance() {
    state.idx += 1;
    if (state.idx >= state.cards.length) {
      el("review_progress").textContent = "";
      el("review_card").innerHTML =
        '<p>Session done.  <a href="/review/index">Back to review index</a></p>';
      return;
    }
    show_current();
  }

  return {
    sync,
    install_fsrs,
    start_session,
  };
})();
