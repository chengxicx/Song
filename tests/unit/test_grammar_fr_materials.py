"""Guards for the French grammar-point library and its derived matchers.

Two layers, mirroring the Russian materials tests:

* the **library** (``lute/jlpt_data/grammar_fr.json``) must be complete and
  key-unique, and every row must carry the fields the panel renders;
* every **derived rule** must fire on at least one of its own examples, run
  through the same matcher the panel uses -- so a mis-derived focus can never
  ship a matcher that marks nothing.

The materials themselves come from local book extractions that are not in
the repository, so the tests skip when the artefacts are absent, and the
provenance check only inspects the ``source`` tag, which must stay an
opaque token: the extraction sources are deliberately not recorded in code.
"""

import json
import os
import re

import pytest

pytest.importorskip("spacy")
pytest.importorskip("fr_core_news_sm")

from lute.read.render import grammar_analysis_fr as fr  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
LIBRARY = os.path.join(_ROOT, "lute", "jlpt_data", "grammar_fr.json")
MATERIALS = os.path.join(_ROOT, "scripts", "grammar_materials_fr", "units.json")

needs_library = pytest.mark.skipif(
    not os.path.exists(LIBRARY), reason="no fr grammar library yet"
)
needs_materials = pytest.mark.skipif(
    not os.path.exists(MATERIALS), reason="no fr grammar materials yet"
)


def _library():
    with open(LIBRARY, encoding="utf-8") as fh:
        return json.load(fh)


@needs_library
def test_library_rows_have_the_render_fields():
    rows = _library()
    assert rows, "library is empty"
    for row in rows:
        assert row.get("name"), row
        assert row.get("level"), row
        assert (row.get("meaning") or "").strip(), row
        assert (row.get("zh") or "").strip(), f"no zh gloss: {row.get('key')}"
        assert row.get("examples"), f"no examples: {row.get('key')}"


@needs_library
def test_library_keys_are_unique():
    keys = [r["key"] for r in _library()]
    assert len(keys) == len(set(keys))


@needs_library
def test_library_keys_do_not_shadow_hand_written_rules():
    written = {r["key"] for r in fr._FR_RULES}
    assert not written & {r["key"] for r in _library()}


@needs_materials
def test_material_sources_are_opaque_tags():
    "The extraction source must never be recorded as a readable name."
    with open(MATERIALS, encoding="utf-8") as fh:
        rows = json.load(fh)
    for row in rows:
        src = (row.get("source") or "").strip()
        assert src, f"missing source tag: {row.get('pattern')}"
        assert re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*", src
        ), f"source tag is not an opaque token: {src!r}"


def test_focus_gap_and_alternative_vocabulary():
    "The '...' gap and 'a/b' alternatives derive matchers that actually fire."
    # gap: "trop ... pour" fires across an adjective, not on a bare pair.
    gap = fr._focus_specs(["trop", "...", "pour"])
    assert gap and "left" in gap[0] and gap[0]["max_gap"] == 4
    text = fr._norm_text("C'est un bébé trop fragile pour aller à la crèche.")
    assert fr.match_rule({"match": gap[0]}, fr._tokens_for(text), text)
    # slash alternatives: "aucun/aucune" matches both surfaces.
    alt = fr._focus_specs(["aucun/aucune"])
    assert len(alt) == 1  # surface-only: no lemma variant for alternatives
    for sentence in ("Je n'ai aucun ami.", "Elle n'a aucune raison."):
        text = fr._norm_text(sentence)
        assert fr.match_rule({"match": alt[0]}, fr._tokens_for(text), text)
    # hyphenated words: fires on both spaCy tokenisations, never on the
    # verb phrase "peut être".
    hyphen = fr._focus_specs(["peut-être"])
    for sentence in (
        "Peut-être viendra-t-il demain.",
        "Peut-être qu'elle est à vendre.",
    ):
        text = fr._norm_text(sentence)
        assert any(
            fr.match_rule({"match": s}, fr._tokens_for(text), text) for s in hyphen
        )
    text = fr._norm_text("Il peut être là demain.")
    assert not any(
        fr.match_rule({"match": s}, fr._tokens_for(text), text) for s in hyphen
    )


@needs_library
def test_data_rules_fire_on_their_own_examples():
    "Each derived matcher must mark at least one of the row's own sentences."
    assert fr._get_data_rules(), "no data rules were derived"
    for rule in fr._get_data_rules():
        examples = fr._FR_RULE_EXAMPLES.get(rule["key"], [])
        assert examples, f"rule {rule['key']} carries no examples"
        fired = False
        for sentence in examples:
            text = fr._norm_text(sentence)
            if fr.match_rule(rule, fr._tokens_for(text), text):
                fired = True
                break
        assert fired, f"rule {rule['key']} fires on none of its examples"


@needs_library
def test_data_rows_pair_their_zh_gloss():
    "Rows merged with parallel zh lines expose example dicts with the gloss."
    entries = {e["key"]: e for e in fr.library_entries("zh")}
    paired = [
        e
        for e in entries.values()
        if e["examples"]
        and isinstance(e["examples"][0], dict)
        and e["examples"][0].get("text")
    ]
    assert paired, "no data rows reached library_entries"
    with_gloss = [e for e in paired if any(r.get("tr") for r in e["examples"])]
    assert with_gloss, "data rows carry examples but no zh gloss"


def test_hand_written_rules_are_the_baseline():
    "The hand-written rules are the baseline; the data layer only adds to them."
    assert len(fr._FR_RULES) >= 34
    assert len(fr._get_all_rules()) >= len(fr._FR_RULES)


def _data_rule(needle):
    "The one merged row whose name contains needle."
    for rule in fr._get_data_rules():
        if needle in rule["name"]:
            return rule
    raise AssertionError(f"no merged row named like {needle!r}")


def test_duplicate_rule_list_matches_the_overlap_audit():
    """
    The panel's silence list must equal what the overlap audit derives.

    Pinning the list to the audit -- rather than to a hand-maintained
    guess -- means growing the library re-derives it, and a row that
    stops being a duplicate fails here instead of silently suppressing
    a point the panel should report.  It is empty while the audit finds
    no same-span duplicate: the 2026-10-19 round fixed its one candidate
    by narrowing the row (see the passive row's notes) instead.
    """
    from scripts import audit_grammar_fr_overlaps as audit

    _by_key, cands = audit.candidates()
    derived = {key for key, _decision in cands}
    assert derived == set(fr._DUPLICATE_RULE_IDS)  # pylint: disable=protected-access


@needs_library
@needs_materials
def test_library_is_a_pure_function_of_the_materials():
    """
    The library must be exactly what the merge derives from the materials.

    ``--write`` refreshes as well as appends, so a row whose ``focus`` or
    wording changed upstream is rewritten in place.  This pins that
    invariant: a hand-edited library row (or a merge that never ran)
    fails here instead of silently serving the old matcher.  The other
    direction is not pinned -- a freshly written material row that has
    not been merged yet is normal work in progress.
    """
    from scripts import merge_grammar_fr_materials as merge

    with open(MATERIALS, encoding="utf-8") as fh:
        materials = json.load(fh)
    rows, _skipped = merge.build_rows(materials, set(), set())
    derived = {r["key"]: r for r in rows}
    for entry in _library():
        if not entry["key"].startswith("fgm_"):
            continue
        assert entry["key"] in derived, f"library row without material: {entry['key']}"
        assert entry == derived[entry["key"]], f"drifted from materials: {entry['key']}"


def test_passive_row_requires_an_explicit_agent():
    """
    The compound-past passive row fires only with a par agent.

    "a été + adjective" ("elle a été malade") is the passé composé of
    être, not a passive, and it collided with the hand-written
    passé-composé rule on the identical span; requiring par keeps the
    row to what its name claims.  Agentless passives ("le film a été
    tourné en 2020") stay unmatched -- the documented trade-off in the
    row's notes, under-matching rather than a false row.
    """
    row = _data_rule("passive")
    fires = (
        "Le vaccin contre la rage a été découvert par Pasteur.",
        "Les livres ont été traduits par Marie.",
    )
    silent = (
        "Elle a été malade toute la semaine.",
        "J'ai été content de te voir.",
        "Le film a été tourné en 2020.",
    )
    for sentence in fires:
        text = fr._norm_text(sentence)
        assert fr.match_rule(row, fr._tokens_for(text), text), sentence
    for sentence in silent:
        text = fr._norm_text(sentence)
        assert not fr.match_rule(row, fr._tokens_for(text), text), sentence
    # The hand-written passé composé still reports both readings: the
    # passive row's span is now the whole construction, so the two are
    # nested reports rather than the same span twice.
    passe = next(r for r in fr._FR_RULES if r["key"] == "fr_passe_compose")
    for sentence in ("Elle a été malade toute la semaine.", "Il a été élu par les habitants."):
        text = fr._norm_text(sentence)
        assert fr.match_rule(passe, fr._tokens_for(text), text), sentence


def test_modals_do_not_fire_on_sentence_initial_peut_etre():
    """
    A capitalised sentence-initial "Peut-être" must not read as pouvoir.

    spaCy splits that one form into Peut / - / être, so the modal rule
    used to fire on the adverb (mid-sentence lowercase "peut-être" stays
    a single token and never matched).  The stray hyphen sits strictly
    inside the hit, which the rule's not_inside vetoes.
    """
    modals = next(r for r in fr._FR_RULES if r["key"] == "fr_modals")
    silent = (
        "Peut-être viendra-t-il demain.",
        "Peut-être partir maintenant ?",
        "Il a dit : Peut-être est-il malade.",
    )
    for sentence in silent:
        text = fr._norm_text(sentence)
        assert not fr.match_rule(modals, fr._tokens_for(text), text), sentence
    # The veto is the hyphen, not the modal: ordinary readings still fire,
    # including one whose span contains a different hyphenated token.
    fires = (
        "Il peut venir ce soir.",
        "Il peut être puni.",
        "Elle peut, paraît-il, être en retard.",
        "Il peut attendre vingt-six ans.",
    )
    for sentence in fires:
        text = fr._norm_text(sentence)
        assert fr.match_rule(modals, fr._tokens_for(text), text), sentence


def test_object_pronoun_row_needs_a_subject_before_it():
    """
    The direct-object row is anchored on "subject pronoun + pronoun".

    le / la / les / l' are also articles, and the French model tags them as
    such ("Il la voit" comes back as DET + NOUN), so a bare form is not a
    usable signal.  What is unambiguous is the *pair*: a determiner can
    never follow a subject pronoun, so "je le", "il l'" only occur when the
    second word is the object pronoun.
    """
    row = _data_rule("objets directs")
    fires = (
        "Je le fais exprès.",
        "Il connaissait Ève depuis deux ans quand il l'a épousée.",
        "Nous les avons vus hier.",
    )
    silent = (
        "Le livre est sur la table.",
        "Elle lit les journaux du matin.",
        "C'est la maison de mon frère.",
    )
    for sentence in fires:
        text = fr._norm_text(sentence)
        assert fr.match_rule(row, fr._tokens_for(text), text), sentence
    for sentence in silent:
        text = fr._norm_text(sentence)
        assert not fr.match_rule(row, fr._tokens_for(text), text), sentence


def test_possessive_rows_leave_le_leur_to_the_indirect_object():
    """
    "le leur" is two local readings, and only the indirect one is matched.

    The sequence le / la / les + leur is either the possessive pronoun
    ("la leur") or a direct + indirect object pair ("le leur donner").
    The possessive row lists mien / tien / sien / nôtre / vôtre only, so
    the ambiguous form stays with the pronoun-order row instead of being
    reported by both.
    """
    possessive = _data_rule("possessifs")
    order = _data_rule("le lui, le leur")
    fires = (
        "Notre maison est plus petite que la vôtre.",
        "Mon père travaille à l'hôpital, et le tien?",
    )
    for sentence in fires:
        text = fr._norm_text(sentence)
        assert fr.match_rule(possessive, fr._tokens_for(text), text), sentence
    ambiguous = "Elle va le leur emprunter."
    text = fr._norm_text(ambiguous)
    assert not fr.match_rule(possessive, fr._tokens_for(text), text)
    assert fr.match_rule(order, fr._tokens_for(text), text)


def test_negation_splits_personne_and_rien_off_the_general_rule():
    """
    personne / rien belong to their own rows, not to ne ... pas/plus/jamais.

    The hand-written rule used to list them among its second words, which
    made it report the same span as the dedicated rows.  Narrowing it to
    the words its name claims keeps both readings distinct, and neither
    point loses its highlight: each dedicated row covers the same span.
    """
    negation = next(r for r in fr._FR_RULES if r["key"] == "fr_negation")
    personne = _data_rule("personne (nobody)")
    rien = _data_rule("rien (nothing)")
    for sentence in ("Je ne connais personne ici.", "Je n'ai vu personne."):
        text = fr._norm_text(sentence)
        assert fr.match_rule(personne, fr._tokens_for(text), text), sentence
        assert not fr.match_rule(negation, fr._tokens_for(text), text), sentence
    for sentence in ("Ça ne veut rien dire.", "Je n'ai rien dit."):
        text = fr._norm_text(sentence)
        assert fr.match_rule(rien, fr._tokens_for(text), text), sentence
        assert not fr.match_rule(negation, fr._tokens_for(text), text), sentence
    for sentence in ("Elle ne travaille pas le lundi.", "Tu ne voyages jamais en hiver."):
        text = fr._norm_text(sentence)
        assert fr.match_rule(negation, fr._tokens_for(text), text), sentence


def test_reported_speech_rows_do_not_cross_fire():
    """
    The three indirect-speech rows share the verb demander but not the frame.

    demander si introduces a yes/no question, demander de + infinitive a
    request, and dit que a statement.  The trap is "dire qu'à": in
    "c'est plus facile à dire qu'à faire" the sequence dire + qu' is not a
    reported clause, so the statement row must stay off it.
    """
    dit = _data_rule("dit que")
    si = _data_rule("demander si")
    de = _data_rule("demander de")
    for sentence in ("On m'a dit que tu étais malade.", "Il a dit qu'il partait."):
        text = fr._norm_text(sentence)
        assert fr.match_rule(dit, fr._tokens_for(text), text), sentence
    idiom = "C'est plus facile à dire qu'à faire."
    text = fr._norm_text(idiom)
    assert not fr.match_rule(dit, fr._tokens_for(text), text)
    for sentence in ("Ils m'ont demandé si j'irais à la soirée.",):
        text = fr._norm_text(sentence)
        assert fr.match_rule(si, fr._tokens_for(text), text), sentence
        assert not fr.match_rule(de, fr._tokens_for(text), text), sentence
    for sentence in ("Il m'a demandé de ne pas être en retard.",):
        text = fr._norm_text(sentence)
        assert fr.match_rule(de, fr._tokens_for(text), text), sentence
        assert not fr.match_rule(si, fr._tokens_for(text), text), sentence


def test_quel_que_soit_needs_the_whole_three_word_frame():
    """
    quel / quelle only count when the subjunctive frame closes behind them.

    quel on its own is an interrogative or exclamative adjective ("Quel
    est le risque ?"), so the row is built on the full quel + que + soit
    sequence and the agreement variants ride on the first slot.
    """
    row = _data_rule("quel que soit")
    fires = (
        "Quelle que soit la difficulté, il faut la surmonter.",
        "Les vrais accros ne s'arrêtent pas de fumer, quel que soit le risque.",
    )
    for sentence in fires:
        text = fr._norm_text(sentence)
        assert fr.match_rule(row, fr._tokens_for(text), text), sentence
    silent = (
        "Quel est le risque pour la santé ?",
        "Quelle belle journée !",
        "Quoi que tu fasses, dis-le-moi.",
    )
    for sentence in silent:
        text = fr._norm_text(sentence)
        assert not fr.match_rule(row, fr._tokens_for(text), text), sentence


def test_a_partir_de_stays_off_depuis():
    """
    Both translate as "from", but only one of them reaches the present.

    depuis measures an ongoing stretch (depuis deux ans + present), while
    à partir de just marks a starting point and lives happily in the
    future.  Mixing them would put the wrong label on half the sentences.
    """
    row = _data_rule("à partir de")
    for sentence in (
        "Le restaurant sera fermé à partir de demain.",
        "Les magasins sont ouverts à partir de neuf heures.",
    ):
        text = fr._norm_text(sentence)
        assert fr.match_rule(row, fr._tokens_for(text), text), sentence
    for sentence in (
        "J'habite ici depuis deux ans.",
        "Il travaille depuis le matin.",
    ):
        text = fr._norm_text(sentence)
        assert not fr.match_rule(row, fr._tokens_for(text), text), sentence


def test_apres_que_stays_off_apres_avoir():
    """
    après que takes a clause; après + infinitive passé is a different point.

    "Après avoir mangé" is the compound past infinitive, already covered by
    the hand-written après avoir / après être rule, so the clause row must
    not reach it -- which is why the second focus word is que/qu' and not a
    bare après.
    """
    row = _data_rule("après que")
    for sentence in (
        "Nous lui avons parlé après qu'il est rentré.",
        "Après qu'il aura fini son rapport, il le présentera.",
    ):
        text = fr._norm_text(sentence)
        assert fr.match_rule(row, fr._tokens_for(text), text), sentence
    text = fr._norm_text("Après avoir mangé, il est parti.")
    assert not fr.match_rule(row, fr._tokens_for(text), text)


def test_quant_a_covers_the_contracted_plural():
    """
    quant à + les contracts to quant aux, so both surfaces are in the row.

    A bare "quant" is not the expression: quantité / quantième start with
    the same letters and must not be labelled as "as for".
    """
    row = _data_rule("quant à")
    for sentence in (
        "Quant à moi, je pense que c'est une bonne idée.",
        "Quant aux risques de malaria, je pense que c'est exagéré.",
    ):
        text = fr._norm_text(sentence)
        assert fr.match_rule(row, fr._tokens_for(text), text), sentence
    text = fr._norm_text("La quantité de sucre est élevée.")
    assert not fr.match_rule(row, fr._tokens_for(text), text)


def test_quantity_adverbs_take_de_without_an_article():
    """
    assez / peu only quantify a noun through de (or d' before a vowel).

    Used adverbially -- "il est assez grand", "il travaille peu" -- there is
    no de, and no noun to quantify, so the row stays off.  "un peu de" does
    fire: it is the same de + noun frame with a softer quantity.
    """
    assez = _data_rule("assez de")
    peu = _data_rule("peu de")
    for sentence in ("Y a-t-il assez de place ?", "Avez-vous assez d'argent ?"):
        text = fr._norm_text(sentence)
        assert fr.match_rule(assez, fr._tokens_for(text), text), sentence
    for sentence in ("Il est assez grand.", "Elle court assez vite."):
        text = fr._norm_text(sentence)
        assert not fr.match_rule(assez, fr._tokens_for(text), text), sentence
    for sentence in ("Il y avait peu de monde.", "Nous connaissons un peu de russe."):
        text = fr._norm_text(sentence)
        assert fr.match_rule(peu, fr._tokens_for(text), text), sentence
    text = fr._norm_text("Il travaille peu.")
    assert not fr.match_rule(peu, fr._tokens_for(text), text)
