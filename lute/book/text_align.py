"""
Align a supplied transcript to ASR cues, and take the text from the
transcript.

The textbook case: the learner has the printed text of what is read on
the recording.  Speech recognition cannot be trusted with names and
homophones ('チョさん' for 張さん, '新郎新父' for 新郎新婦), and a VAD
boundary clips a word onset now and then, so neither side is reliable
alone.  Here the recognizer supplies only *timing*: reference sentences
are located in the recognizer's text, and each one inherits its start and
end from the cue characters it covers.

Locating is done sentence by sentence, in order, from a moving cursor --
not by one global character alignment.  A global alignment goes wrong on
repeated characters: with "前半の文 / 中略の文 / 後半の文" against a
recognizer that dropped the middle sentence, difflib ties "文" of
sentence one to the "文" of sentence three and leaves sentence one
ending halfway through itself.  Locating each sentence from the previous
one's end keeps the result ordered and local, so a dropped or mis-heard
sentence only affects its own timing.

Two rules keep a run of unmatched sentences from dragging the rest of
the book out of sync, which is what "subtitles drift further and further
behind" actually is:

* a sentence only anchors the timeline when most of *it* was found
  (MIN_MATCH_COVERAGE).  A heading like "###メール2" shares "メール" with
  some later line, and accepting that as a match jumps the cursor
  seconds ahead of the audio -- every later sentence then lands late,
  and the error compounds.  A weak match is treated as no match;
* sentences with no anchor of their own get their time *interpolated*
  between the two anchors around them, in proportion to their length,
  rather than a fixed guess per character.  A guess has no idea how much
  audio it is standing in: a run of them invents more (or less) time
  than the recording has, and pushes everything after it off the audio.

The overall similarity (difflib's ratio over the whole text) is returned
for the caller's quality gate; see MIN_MATCH_RATIO.

Nothing here is language specific: it is pure character matching, so it
works for any script.
"""

import difflib

# Below this ratio the transcript and the audio disagree so much that the
# sentence placement is guesswork.  The caller reports an error rather
# than writing a book whose subtitles jump around.
MIN_MATCH_RATIO = 0.5

# A reference sentence ends at one of these, or at a line break.
_SENTENCE_END = "。．.！!？?…‥"

# How far past `cursor` a sentence may be looked for, and how much
# unmatched audio may sit *inside* one sentence's span.  Both exist
# because punctuation repeats: aligning a whole sentence against the
# whole remaining transcript scatters matches across it (the '。' at the
# end of the sentence matches every '。' in the audio), which drags the
# sentence's end far into the recording.  Keeping the search local and
# refusing to bridge a long gap keeps each sentence on its own audio.
_SEARCH_WINDOW_FACTOR = 4
_SEARCH_WINDOW_MIN = 80
_MAX_INTERNAL_GAP = 8

# A sentence may be looked for slightly *before* the previous one ended:
# the reference and the recognizer disagree by a character or two, and
# refusing to look back pushes a sentence onto the wrong audio -- once a
# sentence is located late, every later one inherits the error.
_CURSOR_SLACK = 8

# How much of a sentence must be found before its position counts.
# Below this the match is a shared word in the wrong place ("###メール2"
# against "...前回のメールで..."): anchoring on it skips the audio in
# between and leaves every later sentence late.
MIN_MATCH_COVERAGE = 0.5

# No cue is shorter than this.  Interpolation can otherwise produce a
# zero-length cue where two anchors are adjacent, and a cue of no length
# is never the current one, so its line is silently skipped.
MIN_CUE_SECONDS = 0.35


def _char_times(cues):
    """
    [(start, end)] per character of the cues' joined text.

    A cue covers its text evenly, which is the best guess available:
    SenseVoice returns no token timings.
    """
    times = []
    for cue in cues:
        text = cue.get("text") or ""
        if not text:
            continue
        start = float(cue.get("start") or 0.0)
        end = float(cue.get("end") or 0.0)
        if end < start:
            end = start
        span = (end - start) / len(text)
        for k in range(len(text)):
            times.append((start + span * k, start + span * (k + 1)))
    return times


def _reference_sentences(reference):
    """
    [(sentence, start_index, end_index)] over the reference text.

    Whitespace is dropped -- matching is per character, and spaces in a
    pasted transcript would only dilute it -- while line breaks are kept
    as sentence boundaries, because a pasted textbook transcript is
    usually one sentence per line.
    """
    text = ""
    sentences = []
    sent_start = 0
    for raw_line in (reference or "").replace("\r\n", "\n").split("\n"):
        for ch in raw_line:
            if ch.isspace():
                continue
            text += ch
            if ch in _SENTENCE_END:
                sentences.append((text[sent_start:], sent_start, len(text)))
                sent_start = len(text)
        if len(text) > sent_start:
            sentences.append((text[sent_start:], sent_start, len(text)))
            sent_start = len(text)
    return text, sentences


def _locate(asr_text, cursor, sentence):
    """
    The ASR character span `sentence` covers, searched from `cursor`
    onwards; an empty span when no character of it is in the audio.

    The sentence is aligned against a window of the remaining audio, and
    the span runs from the first matched character to the last one that
    is not separated from it by a long unmatched gap.  Two things keep
    this honest:

    * the window, because punctuation repeats -- matching a whole
      sentence against the whole remaining transcript scatters blocks
      across it (the sentence's closing '。' matches every '。' in the
      audio) and drags the end far into the recording;
    * the gap limit, for the same reason one level down: a sentence's
      own span must not jump over a stretch of audio that belongs to
      something else.

    Matching only the longest run would not do either: with
    "李さんや張さんのおかげで" against "...李さんやチョさんのおかげで" the
    longest run is "さんのおかげで", which starts *inside* the sentence
    and would put its beginning several characters too late.

    A span that covers less than MIN_MATCH_COVERAGE of the sentence is
    reported as no span at all: that is a word the sentence happens to
    share with a line somewhere ahead, not the sentence itself, and
    anchoring on it throws away the audio in between.
    """
    if cursor >= len(asr_text) or not sentence:
        return cursor, cursor
    window = max(_SEARCH_WINDOW_MIN, len(sentence) * _SEARCH_WINDOW_FACTOR)
    search_from = max(0, cursor - _CURSOR_SLACK)
    head = asr_text[search_from : search_from + window]
    matcher = difflib.SequenceMatcher(None, head, sentence, autojunk=False)
    matches = [op for op in matcher.get_opcodes() if op[0] == "equal"]
    if not matches:
        return cursor, cursor
    start = search_from + matches[0][1]
    end = search_from + matches[0][2]
    matched = matches[0][2] - matches[0][1]
    for op in matches[1:]:
        if op[1] - (end - search_from) > _MAX_INTERNAL_GAP:
            break
        end = search_from + op[2]
        matched += op[2] - op[1]
    if matched < len(sentence) * MIN_MATCH_COVERAGE:
        return cursor, cursor
    return start, end


def _interpolate_gaps(times, sentences, audio_end):
    """
    In place: give every None entry a span between its anchored
    neighbours, proportional to its own length.

    A run of sentences the recognizer did not produce has no audio of its
    own to point at, but the audio *around* it is known: the previous
    sentence's end and the next sentence's start.  Spreading that
    interval by character count keeps the run inside the recording and,
    crucially, leaves the next anchor exactly where the recognizer put
    it -- a fixed per-character guess would instead invent a total that
    has nothing to do with how long the run actually took, and push
    everything after it out of sync.
    """
    count = len(times)
    index = 0
    while index < count:
        if times[index] is not None:
            index += 1
            continue
        run_end = index
        while run_end < count and times[run_end] is None:
            run_end += 1
        gap_start = times[index - 1][1] if index > 0 else 0.0
        gap_end = times[run_end][0] if run_end < count else audio_end
        gap = max(0.0, gap_end - gap_start)
        weights = [max(1, len(sentences[k][0])) for k in range(index, run_end)]
        total = sum(weights)
        cursor = gap_start
        for k in range(index, run_end):
            share = gap * weights[k - index] / total
            times[k] = (cursor, cursor + share)
            cursor += share
        index = run_end


def align_cues_to_reference(cues, reference):
    """
    Re-time a supplied transcript against ASR cues.

    Returns (aligned_cues, match_ratio), where aligned_cues is a list of
    {"start", "end", "text"} dicts whose text comes from the reference and
    whose times come from the cues -- or None when there is nothing to
    align (no reference, no cues, or not one character in common).  The
    caller decides whether the ratio is good enough to use; see
    MIN_MATCH_RATIO.

    Two passes.  The first locates the sentences the recognizer actually
    contains and turns those character spans into times; the second fills
    the sentences in between, and any trailing ones, from the anchors
    around them.  The timeline is then walked forwards once so it never
    goes backwards and no cue is too short to be seen.
    """
    ref_text, sentences = _reference_sentences(reference)
    asr_text = "".join((cue.get("text") or "") for cue in cues or [])
    if not ref_text or not asr_text or not sentences:
        return None, 0.0

    char_times = _char_times(cues)
    if not char_times:
        return None, 0.0
    ratio = difflib.SequenceMatcher(None, asr_text, ref_text, autojunk=False).ratio()
    if ratio == 0.0:
        return None, 0.0

    spans = []
    cursor = 0
    for sentence, _start_idx, _end_idx in sentences:
        span_start, span_end = _locate(asr_text, cursor, sentence)
        if span_end > span_start:
            cursor = max(cursor, span_end)
            spans.append((span_start, span_end))
        else:
            # Nothing of this sentence is in the audio at all.  The
            # cursor stays put, so the *next* sentence is still located
            # against the right audio rather than after a phantom.
            spans.append(None)

    times = []
    for span in spans:
        if span is None:
            times.append(None)
            continue
        start = char_times[min(span[0], len(char_times) - 1)][0]
        end = char_times[min(span[1], len(char_times)) - 1][1]
        times.append((start, end))
    _interpolate_gaps(times, sentences, char_times[-1][1])

    aligned = []
    previous_end = 0.0
    for (sentence, _start_idx, _end_idx), (start, end) in zip(sentences, times):
        # Keep the timeline monotonic: a sentence can land on the cue
        # character a previous one already claimed.
        if start < previous_end:
            start = previous_end
        if end < start:
            end = start
        if end - start < MIN_CUE_SECONDS:
            end = start + MIN_CUE_SECONDS
        aligned.append(
            {"start": round(start, 3), "end": round(end, 3), "text": sentence}
        )
        previous_end = end
    return aligned, ratio
