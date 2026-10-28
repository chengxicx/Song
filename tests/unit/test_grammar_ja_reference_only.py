"""Guards for the Japanese library rows recovered from reference-only.

A data row whose pattern yields no matchable fragment is listed as
"reference only": browsable on /grammar, never flagged in a book.  Some of
that is deliberate (vocabulary, duplicates, conjugation tables); a handful
are real constructions the derivation simply cannot express -- 〜末(に),
〜際(に), お〜ください -- and those are recovered with a reviewed
``_SLOT_SPECS`` entry.

Each recovered row is checked here on three counts:

* it is no longer ``skipped`` (the slot validated against its own examples
  at load time -- a stale slot degrades to reference-only, so this failing
  means the spec no longer matches the library);
* it fires on its own examples, i.e. it does what the page promises;
* it does **not** fire on the homograph that made the derivation skip it in
  the first place (実際 for 際, 週末 for 末, ご飯をください for お〜ください).

The last one is the whole point: a recovered row that fires on the wrong
reading is worse than a reference-only row, because the panel then teaches
the wrong thing.
"""

import pytest

from lute.read.render import grammar_analysis_ja as ja

pytest.importorskip("sudachipy")

# entry id -> (own sentences, sentences that must NOT fire)
_RECOVERED = {
    "location-nouns-ue-shita": (
        ["机の上に本があります。", "学校の前で会いましょう。"],
        ["この上ない喜びだ。"],
    ),
    "aida-ni": (
        ["私が留守の間に誰か来ましたか。", "母が料理している間に、宿題をした。"],
        ["時間に遅れました。"],
    ),
    "sue-ni-after": (
        ["長い間悩んだ末、留学することにした。", "議論の末、結論が出た。"],
        ["週末に会いましょう。"],
    ),
    # The V-た form is voiced on voiced stems (読んだ / 遊んだ); both spellings
    # are listed so the regex cannot drift back to matching た only.
    "sai-ni": (
        [
            "非常の際は、このボタンを押してください。",
            "出張の際に、京都に寄った。",
            "この本を読んだ際に、気づいた。",
        ],
        ["実際にやってみた。"],
    ),
    "ori-ni": (
        [
            "お近くにお越しの折には、お寄りください。",
            "結婚式の折には、彼も出席した。",
            "先日遊んだ折に、この店に寄った。",
        ],
        ["折り紙を折る。"],
    ),
    "o-v-kudasai-polite-honor": (
        ["お待ちください。", "ご注意ください。"],
        ["ご飯をください。"],
    ),
    "humble-o-suru": (
        ["先生に本をお貸しします。", "ご案内します。"],
        ["ご飯を食べます。", "お茶を飲みます。"],
    ),
}


def _rules():
    return {rule["entry_id"]: rule for rule in ja._get_data_rules()}


@pytest.mark.parametrize("entry_id", sorted(_RECOVERED))
def test_recovered_row_is_matchable(entry_id):
    "The reviewed slot validated, so the row is no longer reference-only."
    rule = _rules()[entry_id]
    assert rule["patterns"], f"{entry_id} still has no matcher"
    assert rule["skipped"] is False, f"{entry_id} degraded back to reference-only"


@pytest.mark.parametrize("entry_id", sorted(_RECOVERED))
def test_recovered_row_fires_on_its_own_examples(entry_id):
    "What the /grammar page promises: the row recognises the sentences it shows."
    rule = _rules()[entry_id]
    for sentence in _RECOVERED[entry_id][0]:
        assert ja._match_spans(  # pylint: disable=protected-access
            rule, ja._tokens_for(sentence), sentence
        ), f"{entry_id} does not fire on its own example: {sentence}"


@pytest.mark.parametrize("entry_id", sorted(_RECOVERED))
def test_recovered_row_ignores_the_homograph(entry_id):
    "The reading that made the derivation skip the row must stay silent."
    rule = _rules()[entry_id]
    for sentence in _RECOVERED[entry_id][1]:
        assert not ja._match_spans(  # pylint: disable=protected-access
            rule, ja._tokens_for(sentence), sentence
        ), f"{entry_id} fires on the wrong reading: {sentence}"


def test_recovery_does_not_shrink_the_reference_only_set_by_accident():
    """
    The recovered rows are the reviewed ones, no more.

    The set is 477 minus the reviewed batch; if a data update silently
    skipped more rows the count would jump, and this is where that shows up
    before a reader notices a point went missing.
    """
    rules = ja._get_data_rules()
    skipped = sum(1 for rule in rules if rule.get("skipped"))
    assert skipped == 470, f"reference-only rows changed: {skipped} (expected 470)"
