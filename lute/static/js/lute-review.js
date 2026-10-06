/**
 * Review session runner + review index page actions.
 *
 * A session prefetches all due cards in one /review/start call; each
 * grade is a small /review/grade post.  Recognition cards are graded
 * with the two FSRS buttons (Again / Good); cloze cards can be typed
 * and are auto-graded (Again on a wrong answer), or revealed and
 * self-graded.  Shadowing cards play the sentence, record a take
 * (shared LuteRecorder), have it transcribed and diffed word by word,
 * suggest a grade from the score -- the user's buttons stay live --
 * and offer one-click "add the stumbled word to your learning words".
 *
 * The card is one fixed shell with three regions (question, answer,
 * grading) so revealing never makes the buttons jump: the shell is
 * built once per card and only its contents change.
 *
 * Keyboard: Space/Enter reveals, 1-2 grade, Enter checks a typed
 * answer, Space/Enter moves on after a typed check.
 *
 * Cards are pronounced at the answer: the term is spoken when a card
 * is revealed -- recognition, cloze and recall alike -- so the front
 * is never read out before the user has had a chance to recall it.
 * The card carries a 🔊 button on the front and on the answer to hear
 * the term on demand.  A shadowing card is the one exception: it plays
 * its sentence when it opens, because that IS the model audio to
 * shadow, and it speaks the term at reveal too.
 * Each card is spoken in its own term's language (c["lang_code"]),
 * because one queue holds the terms of every language the user
 * studies; tts.js supplies the voice and the /tts/ fallback.  The
 * automatic reading can be turned off in the review settings
 * (review_speak_cards); the button works either way.
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
    cloze: "Fill in the blank",
    shadowing: "Read the sentence aloud",
  };

  const CARD_TYPE_LABELS = {
    recognition: "Recognition",
    cloze: "Cloze",
    shadowing: "Shadowing",
  };

  // Score -> suggested FSRS grade.  Advisory only: the buttons stay
  // live, and an ASR mishear must never force the user's hand.
  function shadowing_suggested_rating(score) {
    if (score == null) return null;
    if (score >= 85) return 3;
    if (score < 60) return 1;
    return null;
  }

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

  /* ---------- pronunciation ---------- */

  // Browsers only allow speech after the user has interacted with the
  // document (Chrome and Safari drop an utterance requested before
  // that).  The first card of a session is exactly that case -- it
  // arrives from a fetch on a page nobody has touched yet -- so instead
  // of losing it, remember that it is owed and speak it at the first
  // key press or click.
  let owed_speak = false;

  // The speaker SVG from the term form's pronunciation button, so the
  // two "say this word" buttons look alike.
  const SPEAKER_SVG =
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M11 4.702a.7.7 0 0 0-1.204-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.7.7 0 0 0 11 19.298z"/>' +
    '<path d="M16 9a5 5 0 0 1 0 6"/>' +
    '<path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>' +
    "</svg>";

  function speak_button_html() {
    return `<button type="button" class="rv-speak" title="Speak (pronounce the term)"
              aria-label="Pronounce the term">${SPEAKER_SVG}</button>`;
  }

  // Speak the term of the card on screen.  tts.js is loaded with defer
  // (review/session.html) and this is only ever called from a render or
  // an event, so the window.luteTts* globals are there; when they are
  // not (an old cached copy of the page), the button is simply inert
  // rather than throwing.  luteTtsSpeakTerm prefers the card's
  // annotated reading (kana) when it has one, so kanji terms whose TTS
  // reading differs from the annotation sound like the annotation.
  function speak_term(c) {
    if (!c || !c.term_text) return;
    if (typeof window.luteTtsSpeakTerm === "function") {
      window.luteTtsSpeakTerm(c.term_text, c.romanization || "", c.lang_code || null);
    } else if (typeof window.luteTtsSpeak === "function") {
      window.luteTtsSpeak(c.term_text, null, c.lang_code || null);
    }
  }

  // The model audio of a shadowing card is the whole sentence.
  function speak_sentence(c) {
    if (!c || !c.sentence_plain) return;
    if (typeof window.luteTtsSpeak === "function") {
      window.luteTtsSpeak(c.sentence_plain, null, c.lang_code || null);
    } else if (typeof speakText === "function") {
      speakText(c.sentence_plain, null, c.lang_code || null);
    }
  }

  // What a card says when it OPENS.  Only shadowing: its model audio is
  // the sentence to shadow, and hearing it is the exercise.  Every other
  // type stays silent until the answer is on screen (see
  // auto_speak_term), so a recognition card is not read aloud before the
  // user has had a chance to recall it.
  function open_speaker(c) {
    if (!c) return null;
    if (c.card_type === "shadowing") return () => speak_sentence(c);
    return null;
  }

  // The review settings page can turn the automatic reading off; the
  // 🔊 button works either way.  Read through tts.js's own setting
  // reader so "0"/"false"/absent parse as they do everywhere else, and
  // default to on when it is missing.
  function speak_cards_enabled() {
    if (typeof window.luteTtsSetting !== "function") return true;
    return window.luteTtsSetting("review_speak_cards", true) !== false;
  }

  // Automatic pronunciation, as the settings allow.  Called from
  // reveal() and check_typed(): the term is spoken once its answer is on
  // screen, never on the front.
  function auto_speak_term(c) {
    if (!speak_cards_enabled()) return;
    speak_term(c);
  }

  // Auto-pronounce the card just opened, as the settings allow.  Only a
  // shadowing card says anything here -- its model sentence; everything
  // else waits for the reveal.
  function auto_speak(c) {
    owed_speak = false;
    if (!speak_cards_enabled()) return;
    const speak = open_speaker(c);
    if (!speak) return;
    const ua = navigator.userActivation;
    if (ua && !ua.hasBeenActive) {
      owed_speak = true;
      return;
    }
    speak();
  }

  // The first interaction of the page releases an owed pronunciation.
  // A click reaches this after the card's own handler has spoken (the
  // card is inside the document), so pressing the 🔊 button does not
  // say the term twice.
  function on_first_gesture() {
    if (!owed_speak) return;
    owed_speak = false;
    const speak = open_speaker(state.current);
    if (speak) speak();
  }

  /* ---------- index page actions ---------- */

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

  function nothing_due_html(counts) {
    // Say *why* the queue is empty: "new waiting" with nothing served
    // means the daily new-card limit is the reason, and that is not
    // obvious from "Nothing due".
    const c = counts || {};
    const bits = [];
    if (c.new_remaining > 0 && !c.new_allowed_today) {
      bits.push(
        `${c.new_remaining} new cards are waiting, but today's limit of ${c.max_new_per_day} is used up`
      );
    }
    if (c.due > 0) {
      bits.push(`${c.due} cards are due`);
    }
    const why = bits.length > 0 ? ` ${bits.join("; ")}.` : "";
    return `<p class="rv-done">Nothing to review right now.${why}
      <a href="/review/index">Back to review index</a></p>`;
  }

  async function start_session() {
    // /review/start builds every card up front, so it can take a
    // moment.  Without this the page is a blank topbar until it lands,
    // which reads as "the session is broken".
    const card = el("review_card");
    if (card) {
      card.innerHTML = '<p class="rv-done">Loading your cards ...</p>';
    }
    try {
      const payload = await post_json("/review/start");
      state.cards = payload.cards;
      state.idx = 0;
      update_undo(payload.undo);
      if (state.cards.length === 0) {
        el("review_progress").innerHTML = "";
        el("review_card").innerHTML = nothing_due_html(payload.counts);
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
      const msg = el("review_progress");
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
    // Recognition has nothing to check and shadowing records instead,
    // so only cloze gets the typed-answer input and Check button.
    const typing =
      c.card_type === "cloze"
        ? `<input type="text" id="review_typing" class="rv-typing" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="your answer">`
        : "";
    const check =
      c.card_type === "cloze"
        ? '<button class="btn btn-primary" id="review_check">Check</button>'
        : "";
    const rec =
      c.card_type === "shadowing"
        ? '<button class="btn btn-primary" id="review_rec_btn" title="Record / stop">&#9679; Record <span id="review_rec_time"></span></button>'
        : "";
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
          ${rec}
          <button class="btn btn-secondary" id="review_reveal">Show answer
            <kbd>Space</kbd></button>
        </div>
        <div id="review_shadow_result" class="rv-shadow-result"></div>
        <div class="rv-answer" id="review_answer" hidden></div>
        <div class="rv-grades" id="review_grades" hidden></div>
      </div>
    `;
  }

  function question_html(c) {
    if (c.card_type === "recognition") {
      return `<div class="rv-term">${esc(c.term_text)}${speak_button_html()}</div>`;
    }
    if (c.card_type === "recall") {
      return `<div class="rv-translation-front">${esc(c.translation)}</div>`;
    }
    if (c.card_type === "shadowing") {
      return `<div class="rv-sentence rv-shadow-sentence" id="review_shadow_tokens"></div>`;
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

    if (c.card_type === "shadowing") shadowing_bind(c);

    auto_speak(c);
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
      pieces.push(
        `<div class="rv-term rv-answer-term">${esc(c.term_text)}${speak_button_html()}</div>`
      );
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
    // Two grades: Again (key 1) and Good (key 2).  The intervals come
    // from the server as {again, good} display strings.
    const suggested =
      c.card_type === "shadowing"
        ? shadowing_suggested_rating(shadowing.score)
        : null;
    const ratings = [
      [1, "Again", c.intervals ? c.intervals.again : ""],
      [3, "Good", c.intervals ? c.intervals.good : ""],
    ];
    return ratings
      .map(([rating, label, interval], i) => {
        const iv = interval ? ` (${interval})` : "";
        const hint =
          rating === suggested
            ? ' title="The take score suggests this grade -- your call"'
            : "";
        return `<button class="rv-grade rv-grade-${rating}${rating === suggested ? " rv-grade-suggested" : ""}" data-rating="${rating}"${hint}>
                  <span class="rv-grade-key">${i + 1}</span>
                  <span class="rv-grade-label">${label}${esc(iv)}</span>
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
    const rec_btn = el("review_rec_btn");
    if (rec_btn) rec_btn.style.display = "none";
    // Leaving a shadowing card mid-take: the take is abandoned, the
    // grade is the user's own.
    if (c.card_type === "shadowing") shadowing_cancel();
    const answer = el("review_answer");
    answer.innerHTML = back_html(c, null);
    answer.hidden = false;
    show_grades(c);
    // The answer is on screen now, so this is where the term gets
    // pronounced (see auto_speak_term).  On a cloze card the term IS the
    // answer; on a recognition card it was the front all along.
    auto_speak_term(c);
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
      auto_speak_term(c);
    } catch (err) {
      show_error(err);
    }
  }

  async function grade(c, rating) {
    try {
      const payload = { card_id: c.id, rating, typed: null };
      if (c.card_type === "shadowing" && shadowing.score != null) {
        // Reported on the log for the stats page; it never changes
        // the grade itself.
        payload.shadowing_score = shadowing.score;
      }
      const res = await post_json("/review/grade", payload);
      update_undo(res.undo);
      advance();
    } catch (err) {
      show_error(err);
    }
  }

  function advance() {
    state.idx += 1;
    shadowing_reset();
    if (state.idx >= state.cards.length) {
      owed_speak = false;
      el("review_progress").innerHTML = "";
      el("review_card").innerHTML =
        '<p class="rv-done">Session done.  <a href="/review/index">Back to review index</a></p>';
      return;
    }
    show_current();
  }

  /* ---------- shadowing cards ---------- */

  // Transient per-card state, reset on every advance(): the recorder,
  // its UI timer, the take's score (advisory, reported with the
  // grade), and the furigana answer for the sentence's tokens.
  const shadowing = {
    recorder: null,
    timer: null,
    recBtn: null,
    score: null,
    readings: null,
  };

  function shadowing_reset() {
    if (shadowing.recorder) shadowing.recorder.cancel();
    shadowing.recorder = null;
    if (shadowing.timer) {
      clearInterval(shadowing.timer);
      shadowing.timer = null;
    }
    shadowing.recBtn = null;
    shadowing.score = null;
    shadowing.readings = null;
  }

  function shadowing_reset_rec_button() {
    if (shadowing.timer) {
      clearInterval(shadowing.timer);
      shadowing.timer = null;
    }
    if (shadowing.recBtn) {
      shadowing.recBtn.innerHTML = "&#9679; Record <span id=\"review_rec_time\"></span>";
    }
  }

  // Abandon a take that is still in flight -- the answer was revealed
  // (by hand or by the take that just scored), so the recording is
  // dropped rather than submitted.  A score already computed is kept:
  // it rides along on the grade the user is about to press.
  function shadowing_cancel() {
    if (shadowing.recorder) {
      shadowing.recorder.cancel();
      shadowing.recorder = null;
    }
    shadowing_reset_rec_button();
  }

  // The sentence as word spans, so the take's verdicts can paint onto
  // the same indexes the server diffed.  Furigana readings arrive
  // asynchronously and are swapped in (Japanese only; the server
  // answers reading=null for everything else).
  function shadowing_render_tokens(c) {
    const box = el("review_shadow_tokens");
    if (!box) return;
    const readings = shadowing.readings || [];
    box.innerHTML = (c.sentence_tokens || [])
      .map((t, i) => {
        const r = readings[i] ? readings[i].reading : null;
        const text = esc(t);
        const inner =
          r && /[\u3040-\u309F\u30A0-\u30FF]/.test(r)
            ? `<ruby>${text}<rt>${esc(r)}</rt></ruby>`
            : text;
        return `<span class="rv-shadow-tok" data-idx="${i}">${inner}</span>`;
      })
      .join(" ");
  }

  function shadowing_paint_verdicts(c, data) {
    const box = el("review_shadow_tokens");
    if (!box) return;
    const statuses = data.statuses || [];
    const fuzzySpoken = data.spoken_for_fuzzy || {};
    const missSpoken = data.spoken_for_miss || {};
    // 0 miss / 1 fuzzy / 2 match / 3 skip.
    const CLASSES = { 0: "shadow-miss", 1: "shadow-fuzzy", 2: "shadow-ok" };
    box.querySelectorAll(".rv-shadow-tok").forEach((tok) => {
      const i = parseInt(tok.dataset.idx, 10);
      const st = i < statuses.length ? statuses[i] : 3;
      const cls = CLASSES[st];
      if (cls) tok.classList.add(cls);
      const heard = st === 1 ? fuzzySpoken[i] : st === 0 ? missSpoken[i] : null;
      if (heard != null) {
        tok.title = `heard: ${heard}`;
      }
    });
  }

  function shadowing_render_status(html, isError) {
    const box = el("review_shadow_result");
    if (!box) return;
    box.innerHTML = `<p class="rv-shadow-state${isError ? " rv-shadow-error" : ""}">${esc(html)}</p>`;
  }

  function shadowing_mark_words_html(c, data) {
    const statuses = data.statuses || [];
    const seen = {};
    const missed = [];
    (c.sentence_tokens || []).forEach((t, i) => {
      const st = i < statuses.length ? statuses[i] : 3;
      if ((st === 0 || st === 1) && !seen[t]) {
        seen[t] = true;
        missed.push(t);
      }
    });
    if (!missed.length) return "";
    return (
      '<div class="rv-shadow-words">Add a stumbled word to your learning words: ' +
      missed
        .map(
          (t) =>
            `<span class="rv-shadow-chip">${esc(t)}<button type="button" class="rv-shadow-add" data-word="${esc(t)}" data-lang="${c.language_id || ""}" title="Add to your learning words">+</button></span>`
        )
        .join(" ") +
      "</div>"
    );
  }

  async function shadowing_mark_word(btn) {
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const res = await LuteRecorder.markWord(
        btn.dataset.lang,
        btn.dataset.word
      );
      btn.textContent = res.outcome === "created" || res.outcome === "promoted" ? "✓" : "·";
      btn.title =
        {
          created: "added",
          promoted: "added",
          learning: "already learning",
          known: "known — left alone",
          invalid: "?",
        }[res.outcome] || res.outcome;
    } catch (err) {
      btn.disabled = false;
      btn.title = String(err);
    }
  }

  function shadowing_bind(c) {
    shadowing_reset();

    const canRecord = typeof LuteRecorder !== "undefined" && LuteRecorder.canRecord();
    if (canRecord && c.transcribe_available === false) {
      shadowing_render_status(
        "No transcription engine on the server (install SenseVoice or whisper in Settings) -- read the sentence aloud on your own, then self-grade.",
        false
      );
    } else if (canRecord && !(c.sentence_tokens || []).length) {
      shadowing_render_status(
        "This sentence has no scorable words -- read it aloud on your own, then self-grade.",
        false
      );
    } else if (!canRecord) {
      shadowing_render_status(
        "Microphone recording needs a secure context (localhost or HTTPS) and a browser with MediaRecorder support -- read the sentence aloud on your own, then self-grade.",
        false
      );
    }

    const recBtn = el("review_rec_btn");
    if (recBtn && canRecord) {
      recBtn.addEventListener("click", () => shadowing_toggle_record(c));
    }

    // Kana readings for the sentence's words (same endpoint the
    // reading page's panel uses; cheap, no audio, no model).
    if ((c.sentence_tokens || []).length) {
      fetch("/read/shadowing/readings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          language_id: c.language_id || 0,
          tokens: c.sentence_tokens,
          full_text: c.sentence_plain || "",
        }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data || !Array.isArray(data.tokens)) return;
          if (state.current !== c) return; // the card moved on
          shadowing.readings = data.tokens;
          shadowing_render_tokens(c);
        })
        .catch(() => {});
    }
  }

  async function shadowing_toggle_record(c) {
    if (shadowing.recorder && shadowing.recorder.recording) {
      shadowing.recorder.stop();
      return;
    }
    if (shadowing.recorder) return; // a take is already being scored
    if (!c.sentence_tokens || !c.sentence_tokens.length) return;

    const recBtn = el("review_rec_btn");
    shadowing.recBtn = recBtn;
    const rec = new LuteRecorder.Recorder({
      capMs: 30000, // a forgotten stop cannot block the session forever
      onStop: (blob) => {
        shadowing_reset_rec_button();
        shadowing.recorder = null;
        shadowing_score_take(c, blob);
      },
      onFail: (msg) => {
        shadowing_reset_rec_button();
        shadowing.recorder = null;
        shadowing_render_status(msg, true);
      },
    });
    const ok = await rec.start();
    if (!ok) return; // onFail already rendered the reason
    shadowing.recorder = rec;
    shadowing.score = null; // a fresh take replaces any earlier one
    if (recBtn) {
      recBtn.innerHTML = "&#9632; Stop <span id=\"review_rec_time\"></span>";
    }
    const t0 = Date.now();
    shadowing.timer = setInterval(() => {
      const t = el("review_rec_time");
      if (t) t.textContent = Math.floor((Date.now() - t0) / 1000) + "s";
    }, 250);
    shadowing_render_status("Recording…", false);
  }

  async function shadowing_score_take(c, blob) {
    shadowing_render_status("Transcribing…", false);
    try {
      const outcome = await LuteRecorder.scoreTake({
        blob,
        languageId: c.language_id,
        tokens: c.sentence_tokens,
        fullText: c.sentence_plain,
        source: "review",
        onProgress: (state, secs) => {
          const label =
            state === "loading_model"
              ? `Loading the speech model… ${secs}s (first run downloads it)`
              : `Transcribing… ${secs}s`;
          shadowing_render_status(label, false);
        },
      });
      if (outcome.state === "error") {
        shadowing_render_status(outcome.error, true);
        return;
      }
      const d = outcome.result || {};
      shadowing.score = d.score != null ? Number(d.score) : null;
      shadowing_paint_verdicts(c, d);
      shadowing_render_status(
        `Score ${shadowing.score == null ? "--" : shadowing.score}%` +
          (d.language_note ? " -- " + d.language_note : ""),
        false
      );
      const wordsBox = el("review_shadow_result");
      if (wordsBox) {
        wordsBox.insertAdjacentHTML(
          "beforeend",
          shadowing_mark_words_html(c, d)
        );
      }
      // The user may have moved on while the take was scoring -- only
      // auto-reveal the card the take belonged to.
      if (state.current === c && state.mode === "question") reveal(c);
    } catch (err) {
      shadowing_render_status("Network error: " + err, true);
    }
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
      // Key 1 = Again, key 2 = Good.
      if (e.key === "1" || e.key === "2") {
        const btns = document.querySelectorAll(".rv-grade");
        const btn = e.key === "1" ? btns[0] : btns[1];
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

    // The card is rebuilt with innerHTML per card, so its 🔊 buttons and
    // its shadowing chips are bound by delegation on the container that
    // stays put.
    const card = el("review_card");
    if (card) {
      card.addEventListener("click", (e) => {
        const add = e.target.closest(".rv-shadow-add");
        if (add) {
          e.stopPropagation();
          shadowing_mark_word(add);
          return;
        }
        if (!e.target.closest(".rv-speak")) return;
        e.stopPropagation();
        owed_speak = false;
        speak_term(state.current);
      });
    }
  }

  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("keydown", on_keydown);
  document.addEventListener("keydown", on_first_gesture);
  document.addEventListener("click", on_first_gesture);

  return {
    install_fsrs,
    start_session,
    undo,
  };
})();
