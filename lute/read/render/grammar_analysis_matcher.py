"""
Shared token-condition matcher for the European grammar engines.

The Japanese and Korean engines each embed their own matcher; this module
factors the same idea out in a language-neutral form so further languages
can be added as thin engines.  It works over tokens shaped like

    {"surface": str, "lemma": str, "pos": str,
     "morph": {str: str}, "idx": int,
     "parses": [{"lemma", "pos", "morph"}, ...]}

where "parses" is optional and lists alternative readings of an ambiguous
word (the Russian engine fills it from pymorphy3; for the spaCy engines it
is absent).  Condition keys:

* "surface_in":  list of surface strings, compared lowercased
* "surface_re":  compiled regex, fullmatch on the lowercased surface
* "lemma_in":    list of lemmas, compared lowercased
* "pos_in":      list of POS tags
* "morph":       dict of feature subset, e.g. {"Tense": "Past"}
* "morph_not":   dict of feature subset that vetoes the token
* "any":         matches anything

Within one spec the conditions are ANDed.  For tokens carrying "parses",
the lemma/pos/morph conditions hold when ANY parse satisfies them.

A rule's "match" is one of

* {"seq": [spec, ...]}              contiguous run of tokens; punctuation
                                    tokens between specs are skipped
* {"left": [...], "right": [...],
   "min_gap": n, "max_gap": m}      gapped two-anchor pattern (the gap is
                                    counted in non-punctuation tokens)
* {"re": compiled_regex}            literal substring recogniser
* {"any_of": [<match>, ...]}        first-shape alternation

An optional rule-level "not_after" spec vetoes matches whose nearest
left-hand non-punctuation neighbour satisfies it.
"""

import re

_PUNCT_POS = {"PUNCT", "SYM"}

_EXAMPLE_CAP = 3


# ---- sentence splitting ----------------------------------------------

def split_sentences(text):
    """
    Split a page of text into sentences on .!?… or line breaks.

    Line breaks matter for subtitle/transcript books, whose lines usually
    carry no sentence-final punctuation and would otherwise collapse into
    one pseudo-sentence (making every grammar example the whole page).
    Abbreviations like "Mr." or "e.g." split too; the engines accept that
    (the Japanese/Korean matchers have the same limitation).  Full-width
    CJK sentence marks are included so the Chinese/Cantonese engines split
    with the same helper.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?<=[.!?…。！？])|\n", text)
    return [p.strip() for p in parts if p and p.strip()]


# ---- condition matching ----------------------------------------------

def _is_punct(token):
    return (token.get("pos") or "") in _PUNCT_POS


def _morph_subset_any(token, feats):
    "True if any reading of the token carries all the given features."
    morphs = [token.get("morph") or {}]
    morphs.extend(p.get("morph") or {} for p in token.get("parses", []))
    return any(all(m.get(k) == v for k, v in feats.items()) for m in morphs)


# A POS reading counts only when it is a significant reading of the word,
# not a dictionary ghost: pymorphy3 lists every reading, and "чай" carries
# a 14% imperative-verb reading that must not fire imperative rules.
_POS_SCORE_RATIO = 0.3


def _pos_match_any(token, wanted):
    "True if a significant reading of the token has one of the wanted POS."
    best = token.get("score", 1.0) or 1.0
    readings = [(token.get("pos") or "", best)]
    readings.extend(
        (p.get("pos") or "", p.get("score", 0.0)) for p in token.get("parses", [])
    )
    return any(pos in wanted and score > _POS_SCORE_RATIO * best for pos, score in readings)


def _match_condition(cond, token):
    "True if a single spec matches a single token."
    if cond.get("any"):
        return True
    surface = (token.get("surface") or "").lower()
    if "surface_in" in cond and surface not in cond["surface_in"]:
        return False
    if "surface_re" in cond and not cond["surface_re"].fullmatch(surface):
        return False
    if "lemma_in" in cond:
        lemmas = {(token.get("lemma") or "").lower()}
        lemmas.update((p.get("lemma") or "").lower() for p in token.get("parses", []))
        if not set(cond["lemma_in"]) & lemmas:
            return False
    if "pos_in" in cond and not _pos_match_any(token, set(cond["pos_in"])):
        return False
    if "morph" in cond and not _morph_subset_any(token, cond["morph"]):
        return False
    if "morph_not" in cond and _morph_subset_any(token, cond["morph_not"]):
        return False
    return True


def _try_match(conds, tokens, i, j):
    """
    Match the condition sequence against tokens starting at index j.

    Returns the end index (exclusive) of the consumed run, or -1 when the
    sequence does not match.  Punctuation tokens are skipped between
    sequence elements (never before the first one).
    """
    if i == len(conds):
        return j
    if i > 0:
        while j < len(tokens) and _is_punct(tokens[j]):
            j += 1
    if j >= len(tokens):
        return -1
    if not _match_condition(conds[i], tokens[j]):
        return -1
    return _try_match(conds, tokens, i + 1, j + 1)


def _token_runs(match, tokens):
    "(start, end) token-index runs where the match shape holds."
    runs = []
    if "any_of" in match:
        for alt in match["any_of"]:
            runs.extend(_token_runs(alt, tokens))
        return runs
    if "re" in match:
        return []  # regex matches carry no token anchors (handled below)
    if "seq" in match:
        for start in range(len(tokens)):
            end = _try_match(match["seq"], tokens, 0, start)
            if end > start:
                runs.append((start, end))
        return runs
    # Gapped two-anchor pattern.
    left, right = match["left"], match["right"]
    min_gap = match.get("min_gap", 0)
    max_gap = match.get("max_gap", 8)
    for lstart in range(len(tokens)):
        lend = _try_match(left, tokens, 0, lstart)
        if lend < 0:
            continue
        for rstart in range(lend, len(tokens)):
            gap = sum(1 for t in tokens[lend:rstart] if not _is_punct(t))
            if gap < min_gap:
                continue
            if gap > max_gap:
                break
            rend = _try_match(right, tokens, 0, rstart)
            if rend > rstart:
                runs.append((lstart, rend))
    return runs


def _regex_spans(match, sentence_text):
    """
    Character spans for {'re': ...} match shapes, collected recursively so
    a regex alternative inside {"any_of": [...]} counts too.
    """
    if "re" in match:
        return [m.span() for m in match["re"].finditer(sentence_text) if m.group(0)]
    if "any_of" in match:
        out = []
        for alt in match["any_of"]:
            out.extend(_regex_spans(alt, sentence_text))
        return out
    return []


def _left_neighbour(tokens, start):
    "Nearest non-punctuation token before token index start, or None."
    j = start - 1
    while j >= 0:
        if not _is_punct(tokens[j]):
            return tokens[j]
        j -= 1
    return None


def match_rule(rule, tokens, sentence_text):
    """
    Character (start, end) spans of sentence_text matched by one rule.

    Several hits inside one sentence come back as separate spans so the
    panel can mark every occurrence.
    """
    match = rule["match"]
    runs = _token_runs(match, tokens)
    spans = []
    offsets = [t["idx"] for t in tokens]
    for start, end in runs:
        veto = False
        if rule.get("not_after"):
            neighbour = _left_neighbour(tokens, start)
            veto = neighbour is not None and _match_condition(rule["not_after"], neighbour)
        if not veto and end > start:
            last = tokens[end - 1]
            # An engine may report the token's true end offset (used when
            # the matching "surface" is normalised shorter than the text).
            char_end = last.get("end") or offsets[end - 1] + len(last["surface"])
            spans.append((offsets[start], char_end))
    # Literal regex recognisers (top-level or nested in any_of).
    spans.extend(_regex_spans(match, sentence_text))
    # Drop duplicate / contained spans, keep reading order.
    spans = sorted(set(spans))
    merged = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end))
    return merged


# ---- rule helpers ------------------------------------------------------

def spec_surface(*surfaces):
    "Spec matching any of the given surface forms (case-insensitive)."
    return {"surface_in": [s.lower() for s in surfaces]}


def spec_lemma(*lemmas):
    return {"lemma_in": [l.lower() for l in lemmas]}


def spec_pos(*pos):
    return {"pos_in": list(pos)}


def spec_morph(**features):
    return {"morph": features}


def make_rule(key, name, level, desc, match, zh=None, not_after=None):
    "Build one engine rule dict."
    r = {"key": key, "name": name, "level": level, "desc": desc, "match": match}
    if zh:
        r["zh"] = zh
    if not_after:
        r["not_after"] = not_after
    return r


# Korean descriptions for the panel's 한국어 display language, keyed by
# rule key across every matcher-based engine.  Each entry mirrors the
# English `desc` of the rule it names; rules without an entry fall back
# to their English description.
_KO_BY_KEY = {
    # ---- English ----
    "en_there_be": "there is / there are는 '~이 있다'의 뜻으로 존재를 나타내며, 복수에는 there are를 쓰는 표현.",
    "en_lets": "let's + 동사원형으로 '~하자'라는 제안을 나타내는 표현.",
    "en_can_could": "can은 능력이나 허락, could는 과거의 능력이나 정중한 부탁을 나타내는 조동사.",
    "en_want_to": "want to + 동사원형으로 '~하고 싶다'는 소망을 나타내는 표현.",
    "en_must_should": "must는 강한 의무, have to는 필수, should는 조언을 나타내는 조동사.",
    "en_past_simple": "일반과거로, 과거에 끝난 동작이나 상태를 나타냄(went, saw).",
    "en_present_continuous": "현재진행형(am/is/are + -ing)으로 지금 일어나고 있는 동작을 나타냄.",
    "en_past_continuous": "과거진행형(was/were + -ing)으로 과거 어느 순간 진행 중이던 동작을 나타냄.",
    "en_will_future": "will + 동사원형으로 미래나 즉석 결심, 약속을 나타냄.",
    "en_going_to": "be going to + 동사원형으로 계획·의도나 임박한 일을 나타냄.",
    "en_comparative": "비교급(-er / more ... than)으로 두 가지를 비교하며 than과 함께 씀.",
    "en_superlative": "최상급(the -est / the most)으로 무리 안에서 가장 높은 정도를 나타냄.",
    "en_like_ing": "like/love/hate/enjoy 같은 선호 동사 뒤에 -ing 형태를 쓰는 표현.",
    "en_too_to": "too + 형용사 + to로 '~해서 ...할 수 없다'는 과다를 나타내는 표현.",
    "en_as_as": "as ... as로 '만큼 ~한' 동등 비교를 나타내는 표현.",
    "en_either_or": "either ... or로 둘 중 하나를 고르는 선택을 나타내는 표현.",
    "en_present_perfect": "현재완료(have/has + 과거분사)로 과거 동작이 현재와 연결됨을 나타냄.",
    "en_past_perfect": "과거완료(had + 과거분사)로 두 과거 동작 중 더 이른 것을 나타냄.",
    "en_passive": "수동태(be + 과거분사)로 주어가 동작을 당하며, 행위자는 by 뒤에 오거나 생략됨.",
    "en_first_conditional": "제1조건문(if + 현재, will)로 실현 가능한 미래의 조건을 나타냄.",
    "en_second_conditional": "제2조건문(if + 과거, would)으로 실현되기 어려운 가정을 나타냄.",
    "en_so_that": "so + 형용사 + that로 '~할 정도로 ~하다'는 결과를 나타내는 표현.",
    "en_used_to": "used to + 동사원형으로 과거의 습관이나 지금은 아닌 상태를 나타냄.",
    "en_relative_pronouns": "관계대명사(who/which/whose)가 명사를 꾸며 주는 절을 연결함.",
    "en_reported_speech": "전달동사(said/told)의 과거형에 맞춰 시제를 한 단계 과거로 옮기는 간접화법.",
    "en_third_conditional": "제3조건문(if + had done, would have done)으로 과거에 실현되지 않은 가정을 나타냄.",
    "en_wish_past": "wish + 과거로 현재와 반대되는 소망을 나타내는 표현.",
    "en_must_have": "must have / can't have + 과거분사로 과거에 대한 추측을 나타내는 표현.",
    "en_have_sth_done": "have + 목적어 + 과거분사(사역 구문)로 남이 해 주는 것을 나타내는 표현.",
    "en_despite": "despite / in spite of는 절이 아닌 명사나 -ing를 이끄는 양보 표현.",
    "en_unless": "unless는 'if ... not', 즉 '~하지 않는 한'의 뜻.",
    "en_inversion": "부정·제한 부사를 문두에 놓으면 조동사가 앞으로 오는 도치 구문(Never have I seen).",
    "en_future_perfect": "미래완료(will have + 과거분사)로 미래 시점까지의 완료를 나타냄.",
    "en_future_continuous": "미래진행형(will be + -ing)으로 미래 어느 시점에 진행 중일 동작을 나타냄.",
    "en_whereas": "whereas / whilst는 격식 있는 대조나 시간 대비를 나타내는 접속사.",
    "en_cleft": "강조 구문(it was ... that)으로 문장을 나눠 특정 부분을 강조함.",
    "en_mandative_subjunctive": "suggest/recommend/insist 등의 that절에서 동사원형을 쓰는 명령형 가정법.",
    # ---- Spanish ----
    "es_ser_de": "ser + de는 출신이나 재료를 나타냄(es de España).",
    "es_estar_en": "estar + en은 위치를 나타냄(está en casa).",
    "es_hay": "hay / había는 비인칭 haber로 '~이 있다'를 나타내며 명사 앞에 관사를 쓰지 않음.",
    "es_gustar": "gustar류 동사는 좋아하는 것이 주어가 됨(me gusta el café).",
    "es_wh_questions": "qué/cómo/dónde 등의 의문사는 정보를 묻으며 강세 표시를 동반함.",
    "es_preterite": "단순과거로 시작과 끝이 분명한 완료된 과거 동작을 나타냄(comí, comió).",
    "es_imperfect": "과거미완으로 진행·반복되는 과거나 과거의 묘사를 나타냄(comía, vivía).",
    "es_ir_a": "ir a + 부정사로 '~하려고 하다'의 근접 미래를 나타냄(voy a comer).",
    "es_tener_que": "tener que + 부정사로 의무를 나타냄(tengo que estudiar).",
    "es_acabar_de": "acabar de + 부정사로 '방금 ~했다'를 나타냄.",
    "es_para_inf": "para + 부정사는 목적(~하기 위해)을, por는 원인·교환을 나타냄.",
    "es_desde_hace": "desde hace + 기간으로 지금까지 계속되는 기간을 나타냄.",
    "es_mas_que": "más/menos ... que로 '~보다 더/덜' 비교를 나타냄.",
    "es_tan_como": "tan + 형용사 + como로 '만큼 ~한' 동등 비교를 나타냄.",
    "es_le": "간접목적 대명사 le/les는 '그/그들에게'의 뜻으로 gustar류와도 쓰임.",
    "es_subj_present": "현재 접속법으로 의지·감정·의심·필요의 표현 뒤에 쓰임(quiero que).",
    "es_conditional": "조건법(compraría)으로 가정이나 정중한 표현을 나타냄.",
    "es_present_perfect": "현재완료(he + 과거분사)로 현재와 연결된 과거 동작을 나타냄(he comido).",
    "es_pluperfect": "과거완료(había + 과거분사)로 두 과거 중 더 이른 동작을 나타냄.",
    "es_si_conditional": "si + 과거접속법, 조건법으로 비현실적인 가정을 나타냄(Si tuviera, compraría).",
    "es_subj_past": "과거접속법(-ra/-se)으로 과거 트리거 뒤나 si 절에서 쓰임.",
    "es_subj_pluperfect": "과거완료 접속법(hubiera + 과거분사)으로 실현되지 않은 과거 가정을 나타냄.",
    "es_conditional_perfect": "조건법 완료(habría + 과거분사)로 '과거에 ~했을 텐데'를 나타냄.",
    "es_se": "se는 재귀(se lava), 수동(se vende), 비인칭(se dice)을 나타냄.",
    "es_como_si": "como si + 접속법으로 '~인 것처럼'을 나타냄.",
    "es_llevar_gerund": "llevar + 기간 + gerundio로 지금까지 계속된 기간을 나타냄.",
    "es_de_haber": "de haber + 과거분사는 문어체 비현실 과거 조건(= si hubiera sabido).",
    "es_por_mas_que": "por más que + 접속법으로 '아무리 ~해도' 양보를 나타냄.",
    "es_y_eso_que": "y eso que는 '~했는데도'의 구어적 양보 표현.",
    "es_no_es_que": "no es que + 접속법으로 '~해서가 아니라'를 나타냄.",
    # ---- Russian ----
    "ru_u_menya": "у + 속격 + есть로 '~이 가지고 있다' 소유를 나타내고, нет으로 부정함.",
    "ru_nravitsya": "여격 + нравится로 좋아하는 것이 주어가 됨(мне нравится).",
    "ru_prep_loct": "в/на + 전치격으로 '~에(서)' 위치를 나타냄(в городе).",
    "ru_k_dat": "к + 여격으로 사람·장소를 향한 방향을 나타냄(к другу).",
    "ru_modal_inf": "можно/нужно/надо + 부정사의 비인칭 양태 표현.",
    "ru_imperative": "명령형으로 명령을 나타냄(читайте, сядьте).",
    "ru_s_ablt": "с + 조격으로 '~와 함께'를 나타냄(с другом).",
    "ru_gen_preps": "без/для/до/из/от 등의 전치사는 뒤에 속격을 요구함.",
    "ru_o_loct": "о/об + 전치격으로 '~에 대하여'를 나타냄(о работе).",
    "ru_nums_2_4": "2~4 뒤의 명사는 단수 속격을 취함(два часа).",
    "ru_nums_5": "5 이상(много/несколько 포함) 뒤의 명사는 복수 속격을 취함(пять книг).",
    "ru_past": "과거형(-л/-ла/-ли)으로 미완료체는 과정·습관, 완료체는 결과를 나타냄.",
    "ru_future": "미완료체는 буду + 부정사, 완료체는 단일 형태로 미래를 나타냄.",
    "ru_by": "бы + 과거형으로 가정법을 만듦(если бы).",
    "ru_chtoby": "чтобы는 목적('~하기 위해')이나 바라는 일을 나타냄.",
    "ru_poka_ne": "пока не는 '~할 때까지'를 나타냄.",
    "ru_posle_togo_kak": "после того как는 '~한 후에'를 나타냄.",
    "ru_pricastie": "형용분사(-щий/-вший/-мый)로 동작하는/동작한 대상을 꾸밈.",
    "ru_deepricastie": "부사분사(-я/-в)로 동시 동작이나 방식을 나타냄(читая).",
    "ru_motion_prefixes": "이동동사에 접두사(при-/у-/по-)가 붙어 의미가 달라짐(прийти 도착하다).",
    "ru_sya": "-ся/-сь 동사는 재귀를 나타냄(учится).",
    "ru_dolzhen_byl": "должен был + 부정사로 '하기로 되어 있었지만 안 했다'를 나타냄.",
    "ru_chut_ne": "чуть не + 완료체 과거로 '하마터면 ~할 뻔했다'를 나타냄.",
    "ru_stoilo_kak": "стоило ... как는 '~하자마자'를 나타냄.",
    "ru_v_techenie": "в течение + 속격으로 '~동안에'를 나타냄.",
    "ru_double_neg": "никогда/никто 등의 부정대명사는 не와 함께 쓰임(이중 부정).",
    "ru_kak_ni": "как ни는 '아무리 ~해도'의 양보를 나타냄.",
    "ru_edva_li": "едва ли / вряд ли는 '~일 리 없다'의 의심을 나타냄.",
    "ru_chem_tem": "чем ... , тем ...로 '~하면 할수록' 상관 비교를 나타냄.",
    # ---- French ----
    "fr_il_y_a": "il y a는 '~이 있다'를 나타내며 과거는 il y avait.",
    "fr_estceque": "est-ce que는 평서문을 예/아니오 의문문으로 바꿈.",
    "fr_c_est": "c'est는 '~이다'로 사물이나 사람을 지시·설명함.",
    "fr_wh_questions": "quand/comment/pourquoi 등의 의문사가 정보를 물음.",
    "fr_negation": "ne ... pas 등의 부정 구문: ne는 동사 앞, 나머지는 동사 뒤에 옴.",
    "fr_possessives": "소유형용사(mon/ma/mes)는 소유자가 아니라 소유물의 성·수에 일치함.",
    "fr_futur_proche": "근접미래(aller + 부정사)로 '~하려고 하다'를 나타냄(je vais partir).",
    "fr_passe_compose": "복합과거(avoir/être + 과거분사)로 회화체의 과거를 나타냄.",
    "fr_imparfait": "반과거로 지속·습관·묘사의 과거를 나타냄.",
    "fr_futur_simple": "단순미래(sera/irai)를 나타냄.",
    "fr_modals": "pouvoir/vouloir/devoir + 부정사로 능력·소망·의무를 나타냄.",
    "fr_comparative": "plus/moins/aussi ... que로 비교와 동등을 나타냄.",
    "fr_venir_de": "venir de + 부정사로 '방금 ~했다'를 나타냄.",
    "fr_depuis": "depuis는 지속을 나타내며 프랑스어는 현재시제와 씀(j'habite ici depuis deux ans).",
    "fr_relative": "관계대명사 qui(주어)/que(목적)/dont/où로 절을 연결함.",
    "fr_subjonctif": "접속법으로 의지·감정·의심·필요 뒤에 쓰임(il faut que).",
    "fr_il_faut": "il faut는 비인칭 필수('~해야 한다')를 나타냄.",
    "fr_conditionnel": "조건법(voudrais)으로 정중한 부탁과 가정을 나타냄.",
    "fr_ce_que": "ce qui / ce que는 '~하는 것'의 중성 관계대명사.",
    "fr_y_en": "대명사 y(장소)와 en(양·de 목적어)의 대용.",
    "fr_sans_inf": "sans + 부정사로 '~하지 않고'를 나타냄.",
    "fr_si_conditionnel": "si + 반과거, 조건법으로 비현실적인 현재 가정을 나타냄.",
    "fr_gerondif": "en + 현재분사(gérondif)로 '~하면서/~함으로써'를 나타냄.",
    "fr_subjonctif_passe": "과거 접속법으로 접속법 안의 선행 동작이나 비현실을 나타냄.",
    "fr_apres_avoir": "après avoir/être + 과거분사로 '~한 후에'를 나타냄.",
    "fr_passe_simple": "단순과거는 문어체 서사의 과거(il fit).",
    # ---- German ----
    "de_es_gibt": "es gibt + 대격으로 '~이 있다' 존재를 나타냄.",
    "de_modals": "활용된 조동사는 2위, 부정사는 문장 끝에 감(ich kann heute nicht kommen).",
    "de_negation": "nicht는 동사·형용사를, kein은 명사를 부정함.",
    "de_questions": "wer/was/wann/wo/warum 등의 의문사가 정보를 물음.",
    "de_possessives": "소유관사(mein/dein)는 소유물의 성·수·격에 따라 어미를 취함.",
    "de_perfekt": "완료과거(Perfekt: haben/sein + 과거분사)로 회화체의 과거를 나타냄.",
    "de_praeteritum": "과거(Präteritum)는 문어체 과거이며 회화에서는 sein/haben/조동사에 주로 씀.",
    "de_futur1": "Futur I(werden + 부정사)로 미래나 추측을 나타냄.",
    "de_akku": "직접목적어는 대격을 취함(den Mann).",
    "de_dativ": "간접목적어와 aus/bei/mit/nach/seit/von/zu 뒤는 여격을 취함.",
    "de_zu_inf": "zu + 부정사로 versuchen/beginnen 등과 연결됨.",
    "de_als": "als는 비교('~보다')와 과거 시간절('~했을 때')을 나타냄.",
    "de_gern": "gern은 동작에 대한 좋아함을 나타냄(ich schwimme gern).",
    "de_reflexive": "독일어에는 재귀동사가 많음(sich freuen, ich wasche mich).",
    "de_weil": "weil/obwohl/damit 같은 종속접속사 뒤의 동사는 문장 끝으로 감.",
    "de_werden_passiv": "수동태(werden + 과거분사)를 나타냄(es wird gemacht).",
    "de_konjunktiv2": "가정법 II(würde/hätte/wäre)로 정중한 부탁과 비현실을 나타냄.",
    "de_seit": "seit는 지속을 나타내며 독일어는 현재시제와 씀(seit zwei Jahren).",
    "de_ob": "ob는 '~인지 아닌지'의 간접 의문을 나타냄.",
    "de_trotz": "trotz + 속격은 '~에도 불구하고', trotzdem은 '그럼에도'의 뜻.",
    "de_je_desto": "je ... desto로 '~하면 할수록' 상관 비교를 나타냄.",
    "de_zwar": "zwar/jedoch/allerdings는 격식 있는 양보·대조를 나타냄.",
    "de_indem": "indem은 '~함으로써' 방식절을 이끌며 동사가 끝으로 감.",
    # ---- Italian ----
    "it_essere_di": "essere + di는 출신을 나타냄(sono di Roma).",
    "it_ce": "c'è / ci sono는 '~이 있다'(단수/복수)를 나타냄.",
    "it_piacere": "piacere는 좋아하는 것이 주어가 됨(mi piace la pizza).",
    "it_questions": "che/come/dove/perché 등의 의문사가 정보를 물음.",
    "it_stare_gerundio": "stare + gerundio로 진행 중인 동작을 나타냄(sto mangiando).",
    "it_passato_prossimo": "근접과거(avere/essere + 과거분사)로 회화체의 과거를 나타냄.",
    "it_imperfetto": "미완과거로 지속·습관·묘사의 과거를 나타냄.",
    "it_futuro": "단순미래(partirò, sarà)를 나타냄.",
    "it_modali": "potere/volere/dovere + 부정사로 능력·소망·의무를 나타냄.",
    "it_comparativo": "più/meno ... che/di로 비교를 나타냄.",
    "it_bisogna": "bisogna는 비인칭 필수('~할 필요가 있다')를 나타냄.",
    "it_congiuntivo": "접속법으로 의견·의심·감정 뒤에 쓰임(penso che sia).",
    "it_condizionale": "조건법(vorrei, comprerei)으로 정중한 부탁과 가정을 나타냄.",
    "it_cui": "관계대명사 cui(전치사와 함께)와 il quale(명사와 일치)를 나타냄.",
    "it_da_tempo": "da + 기간은 현재시제와 함께 지속을 나타냄(abito qui da due anni).",
    "it_ne": "부분대명사 ne가 di구나 수량을 대신함.",
    "it_prima_di": "prima di + 부정사로 '~하기 전에'를 나타냄.",
    "it_si_passivante": "si는 수동·비인칭을 나타냄(si parla, si dice).",
    "it_stare_per": "stare per + 부정사로 '막 ~하려는 참'임을 나타냄.",
    "it_congiuntivo_passato": "과거 접속법으로 접속법 안의 선행 동작을 나타냄.",
    "it_se_condizionale": "se + 접속법, 조건법으로 비현실적인 가정을 나타냄.",
    "it_congiuntivo_imperfetto": "미완 접속법(fossi/avessi)으로 비현실이나 과거 시제 일치의 접속법을 나타냄.",
    "it_dopo_aver": "dopo avere/essere + 과거분사로 '~한 후에'를 나타냄.",
    "it_passato_remoto": "원과거(passato remoto)는 역사 서사의 과거(fu, vide).",
    # ---- Portuguese ----
    "pt_estar_em": "estar + em은 위치를 나타냄(está em casa).",
    "pt_haver": "비인칭 há(구어 tem)는 '~이 있다'를 나타냄.",
    "pt_gostar": "gostar는 전치사 de를 취함(gosto de música).",
    "pt_questions": "onde/como/por que 등의 의문사가 정보를 물음.",
    "pt_progressivo": "진행형: 유럽식은 estar a + 부정사, 브라질식은 estar + gerúndio.",
    "pt_ir_inf": "ir + 부정사로 흔히 쓰이는 미래 표현(vou comer).",
    "pt_imperfeito": "미완과거로 지속·습관·묘사의 과거를 나타냄.",
    "pt_modais": "poder/dever/querer/precisar + 부정사로 능력·의무·소망을 나타냄.",
    "pt_ter_que": "ter que/de + 부정사로 의무를 나타냄(tenho que estudar).",
    "pt_comparativo": "mais/menos/tão ... que/como로 비교와 동등을 나타냄.",
    "pt_subjuntivo": "접속법으로 의지·의심·감정 뒤에 쓰임(espero que venha).",
    "pt_relative": "관계대명사 o qual(명사와 일치), cujo('~의'), quem을 나타냄.",
    "pt_ha_tempo": "há + 시간은 '~전에' 또는 '~동안'을 나타냄.",
    "pt_prima_inf": "antes de/depois de + 부정사로 '~하기 전에/후에'를 나타냄.",
    "pt_se_passivo": "clitic -se는 수동·비인칭을 나타냄(fala-se).",
    "pt_mais_que_perfeito": "대과거(tinha + 과거분사)로 두 과거 중 더 이른 동작을 나타냄.",
    "pt_se_condicional": "se + 접속법, 조건법으로 비현실적인 가정을 나타냄.",
    "pt_conjuntivo_imperfeito": "미완 접속법(fosse/tivesse)으로 비현실 조건을 나타냄.",
    # ---- Thai ----
    "th_pronouns": "인칭대명사: ผม(남자 나), ฉัน(여자 나), คุณ(당신), เขา(그/그녀), เรา(우리).",
    "th_copula": "계사: เป็น(종류), อยู่(위치), คือ(동일성).",
    "th_negation": "ไม่가 동사·형용사 앞에서 부정함(ไม่ไป, ไม่ดี).",
    "th_questions": "의문사: อะไร(무엇), ที่ไหน(어디), ใคร(누구), ทำไม(왜), เมื่อไหร่(언제).",
    "th_have": "มี는 '~이 있다' 소유나 존재를 나타냄.",
    "th_polite": "정중 어기사: ครับ(남자), ค่ะ/คะ(여자).",
    "th_and_or": "접속사: และ(~하고), หรือ(~또는), แต่(~그러나).",
    "th_future": "จะ가 동사 앞에서 미래를 나타냄(ฉันจะไป).",
    "th_progressive": "กำลัง(동사 앞)이나 อยู่(동사 뒤)가 진행을 나타냄.",
    "th_completive": "แล้ว가 완료나 상태 변화를 나타냄(กินแล้ว).",
    "th_want": "อยาก + 동사는 '~하고 싶다', ต้องการ는 원하다/필요하다.",
    "th_should_must": "ควร는 '~하는 게 좋다', ต้อง은 '~해야 한다(필수)'.",
    "th_yesno": "문장 끝 ไหม이 예/아니오 의문을 만듦.",
    "th_because": "เพราะ(ว่า)는 '~때문에', เพราะฉะนั้น은 '그러므로'.",
    "th_when": "เมื่อ/ตอนที่가 시간절을 이끎.",
    "th_thi": "ที่는 명사 뒤에서 관계절을 연결함(คนที่มา = 온 사람).",
    "th_can": "สามารถ ... ได้ 프레임이 능력을 나타냄.",
    "th_ever": "เคย가 동사 앞에서 과거 경험을 나타냄.",
    "th_give": "ให้는 '주다', 사동('~하게 하다'), '~을 위해'의 뜻.",
    "th_each_other": "กัน이 동사 뒤에서 '서로'를 나타냄(รักกัน).",
    "th_if": "ถ้า/หาก가 조건을 이끌며 결과절은 흔히 ก็와 짝을 이룸.",
    "th_na_worth": "น่า + 동사는 '~할 만하다/~한 느낌'을 나타냄(น่าสนใจ).",
    "th_also": "ด้วย/ก็가 '~도'를 나타내거나 강조함.",
    "th_passive": "ถูก(중립)나 โดน(불리한)이 수동을 나타냄.",
    "th_both": "ทั้ง ... และ로 '~와 ~ 둘 다'를 나타냄.",
    "th_almost": "เกือบ가 '거의/~할 뻔했다'를 나타냄.",
    "th_concessive": "แม้ว่า/ถึงแม้가 양보를 이끌며 흔히 แต่와 짝을 이룸.",
    "th_in_order_to": "เพื่อ가 목적('~하기 위해')을 나타냄.",
    "th_formal_concessive": "อย่างไรก็ตาม은 문어체 '~하든지 간에/그럼에도'의 양보.",
    # ---- Arabic ----
    "ar_questions": "هل은 예/아니오 의문을, ماذا/متى/أين/كيف/كم/لماذا는 정보를 물음.",
    "ar_prepositions": "في/من/إلى/على/عن/مع는 뒤에 속격 명사나 접미대명사를 취함.",
    "ar_al": "ال-는 정관으로 '~이 그'임을 나타냄(البيت = 그 집).",
    "ar_demonstratives": "지시대명사: هذا/هؤلاء(남성), هذه(여성), ذلك/تلك(저것).",
    "ar_neg_la": "لا가 미완료 동사 앞에서 부정함.",
    "ar_kana": "كان(~였다), أصبح(~되었다) 계사동사로 술어는 주격을 유지함.",
    "ar_pronouns": "독립 인칭대명사(أنا/هو/هي/نحن/هم).",
    "ar_but": "لكن은 '~그러나'를 나타냄.",
    "ar_because": "لأن는 '~때문에', لذلك/لهذا는 '그러므로'.",
    "ar_when": "عندما/حين이 시간절을 이끎.",
    "ar_sawfa": "سوف(또는 접두사 سـ)가 현재 동사를 미래로 만듦.",
    "ar_also": "أيضا/كذلك은 '~또한'을 나타냄.",
    "ar_every": "كل(~모든), بعض(~일부) 뒤에는 속격이 옴.",
    "ar_anna": "أن/إن이 내용절을 이끌며 إنّ은 강조도 나타냄.",
    "ar_if": "إذا는 실현 가능한 조건, لو는 비현실 가정을 나타냄.",
    "ar_lan_lam": "لن + 접속형은 '~하지 않을 것이다', لم + 절단형은 '~하지 않았다'.",
    "ar_relative": "관계대명사 الذي/التي/الذين이 선행사와 일치함.",
    "ar_qad": "قد + 과거는 '이미/과연', قد + 현재는 '~일지도 모른다'.",
    "ar_suffix_ha": "접미사 ها가 명사·전치사에 붙어 '그녀의/그것의'를 나타냄.",
    "ar_suffix_plural": "복수 소유 접미사 ـهم/ـكم/ـنا가 단어에 직접 붙음.",
    "ar_hatta": "حتى는 '~까지', 또는 명사 앞에서 '~조차'의 뜻.",
    "ar_laysa": "ليس는 명사문을 부정함(ليس صعبا).",
    "ar_wish": "ليت는 '~이면 좋겠다', لعل은 '~일지도 모른다'.",
    "ar_kullama": "كلما는 '~하면 할수록/Whenever'의 상관 표현.",
    "ar_lam_yakun": "لم يكن + 분사는 '~하지 않았었다' 과거완료 부정.",
    # ---- Mandarin ----
    "zh_le": "동사 + 了가 완료된 동작이나 새로운 상황을 나타냄(我吃了).",
    "zh_zhe": "동사 + 着가 지속 상태나 방식을 나타냄(坐着, 拿着).",
    "zh_guo": "동사 + 过가 경험한 과거를 나타냄(去过中国).",
    "zh_bu": "不가 현재·미래나 습관적 동작을 부정함(我不去).",
    "zh_mei": "没(有)가 과거 동작을 부정하거나 '아직 아님'을 나타냄(我没去).",
    "zh_zai": "在 + 장소는 '~에서', 在 + 동사는 진행을 나타냄(在看书).",
    "zh_question_words": "의문사: 什么(무엇), 哪(어느), 谁(누구), 怎么(어떻게), 几/多少(몇).",
    "zh_ma": "문장 끝 吗가 평서문을 예/아니오 의문문으로 바꿈.",
    "zh_ne": "문장 끝 呢가 되묻거나 어조를 부드럽게 함.",
    "zh_ba_q": "문장 끝 吧가 제안이나 추측의 어조를 더함(走吧).",
    "zh_ye_dou": "也(~도), 都(~모두)는 주제 뒤에 옴.",
    "zh_ge": "個는 일반적인 양사(一个人).",
    "zh_shi_de": "是 ... 的 강조 구문으로 이미 일어난 일의 시간·방식·장소를 강조함(他是昨天来的).",
    "zh_ba_sentence": "把문은 목적어를 동사 앞으로 옮기고 보어를 동반하는 처리문(把作业写完了).",
    "zh_bei_sentence": "被문은 수동을 나타냄(被雨淋了).",
    "zh_zhengzai": "正在가 동작의 진행을 나타냄(他正在看书).",
    "zh_cai_jiu": "就는 예상보다 이른, 才는 예상보다 늦은 시간을 나타냄.",
    "zh_geng_zui": "更(~더), 最(~가장)의 비교 표현.",
    "zh_bibi": "X 比 Y + 형용사로 '~보다 ~하다' 비교를 나타냄.",
    "zh_yinwei_suoyi": "因为(~때문에)가 원인을, 所以(~그래서)가 결과를 나타냄.",
    "zh_hai": "还는 '~아직/~게다가'를 나타냄.",
    "zh_yueyue": "越 ... 越 ...로 '~하면 할수록'을 나타냄(越来越好).",
    "zh_yibian_yibian": "一边 ... 一边 ...로 두 동작의 동시 진행을 나타냄.",
    "zh_ruguo": "如果/要是가 조건을 이끌며 흔히 就와 짝을 이룸.",
    "zh_chule": "除了 ... 以外/都/还로 '~를 제외하고/~외에도'를 나타냄.",
    "zh_yijing": "已经가 기준 시각 대비 완료를 나타냄.",
    "zh_zhiqian_zhihou": "以前/以后/之前/之后의 시간 상대 표현.",
    "zh_rang_jiao": "让/叫이 사동이나 구어 수동을 만듦(让他去, 叫人骗了).",
    "zh_wulun": "无论/不管 + 의문사·선택지 + 都로 '~이든 간에'를 나타냄.",
    "zh_erqie": "而且/并且가 내용을 더하며 而且는 단계적 강화도 나타냄.",
    "zh_jingran": "竟然/居然가 뜻밖임을 나타냄(他竟然来了).",
    "zh_genju": "根据/按照가 행동의 근거를 나타내는 전치사.",
    "zh_yimian": "以免가 '~되지 않도록' 회피 결과를 이끎.",
    "zh_bijing": "毕竟/反正가 '~어쨌든/~어차피'의 담화 부사.",
    "zh_shenzhi": "甚至가 '~심지어/~조차'의 단계적 강화를 나타냄.",
    "zh_hekuang": "何况/况且가 '~하물며/게다가'의 형식적 첨가 논거.",
    "zh_nanyi": "加以/予以/难以의 문어체 동사 구성(加以解决, 难以接受).",
    "zh_yizhi": "以至/以致가 결과를 이어 주며 以致는 주로 나쁜 결과를 나타냄.",
    "zh_ershi": "然而/却가 문어체 대조를 나타냄.",
    # ---- Cantonese ----
    "yue_ge": "嘅은 普通话 的에 해당하며 소유·수식을 나타냄(我嘅書).",
    "yue_zo": "동사 + 咗는 了에 해당하며 완료를 나타냄(食咗飯).",
    "yue_gan": "동사 + 緊은 '~하고 있는 중' 진행을 나타냄(食緊飯).",
    "yue_m": "唔가 동사·형용사를 부정함(唔去, 唔好).",
    "yue_pronouns": "인칭대명사: 佢(그/그녀), 복수는 哋(我哋/你哋/佢哋).",
    "yue_questions": "의문사: 乜嘢/咩(무엇), 邊度(어디), 邊個(누구), 點解(왜), 幾多(얼마).",
    "yue_hai": "喺는 在에 해당하며 위치를 나타냄(喺屋企).",
    "yue_yiga": "而家는 '지금'을 나타냄.",
    "yue_zyu": "동사 + 住가 동작 중의 상태 유지를 나타냄(拎住).",
    "yue_sai": "동사 + 晒는 '전부' 완료를, 完은 '끝남'을 나타냄.",
    "yue_gam": "咁이 형용사 앞에서 '~이리', 噉은 '~저리'를 나타냄.",
    "yue_zung": "仲은 '~아직/~게다가'를 나타냄(仲未食).",
    "yue_tungmaai": "同埋는 '~하고', 定係는 의문문의 '~아니고'를 나타냄.",
    "yue_final_particles": "문장 말 어기사: 啦(제안), 喎(당연), 囉(그러려니) 등.",
    "yue_sik": "識 + 동사는 '~할 줄 알다'를 나타냄.",
    "yue_msai": "唔使는 '~할 필요 없다', 使唔使는 '~할 필요가 있나?'를 나타냄.",
    "yue_dak": "동사 뒤 得이 가능이나 보어 표지(講得快).",
    "yue_maai": "동사 + 埋는 '~까지 포함해서'를 나타냄(買埋呢個).",
    "yue_sik_jyu": "好似 ... 噉이 '~처럼' 비유를 나타냄.",
    "yue_jauh_sik": "就算 ... 都가 '~해도' 양보를 나타냄.",
    "yue_jyuhai": "冇는 没에 해당하며 소유 부정이나 과거 부정(冇去)을 나타냄.",
    "yue_mdaanzi": "唔單止 ... 仲(要)가 '~뿐만 아니라 ~도'를 나타냄.",
    "yue_mhtung": "唔通은 '~일 리는 없지 않을까' 되묻는 의심 표지.",
    "yue_mingming": "明明이 '~분명한데도'를 강조함.",
    "yue_lahngwaah": "담화 표지: 講真(솔직히), 話時話(그나저나).",
    "yue_ngaanghai": "硬係가 '~고집스럽게 ~하다'를 나타냄(佢硬係唔信).",
}


def _desc(rule, display_lang):
    "Description for a rule in the requested display language."
    if display_lang == "ko":
        ko = _KO_BY_KEY.get(rule.get("key"))
        if ko:
            return ko
    if display_lang == "zh" and rule.get("zh"):
        return rule["zh"]
    return rule["desc"]


# ---- analysis driver ---------------------------------------------------

def analyze_tokens(page_text, rules, tokens_for_sentence, display_lang):
    """
    Run the rules over a page of text.

    Returns a list of dicts:
      {"key", "name", "level", "desc", "examples": [{"sentence", "matches"}]}
    Rules appearing on the page are merged by key; examples are deduped
    and capped per rule.  Each example's "matches" lists {"start", "end"}
    character offsets within "sentence" so the front-end can highlight the
    matched words in the reading text.
    """
    page_text = page_text.replace("🔊", "").replace("\u200b", "")
    by_key = {}
    order = []
    for sentence in split_sentences(page_text):
        if not sentence:
            continue
        tokens = tokens_for_sentence(sentence)
        if not tokens:
            continue
        for rule in rules:
            spans = match_rule(rule, tokens, sentence)
            if not spans:
                continue
            entry = by_key.get(rule["key"])
            if entry is None:
                entry = {
                    "key": rule["key"],
                    "name": rule["name"],
                    "level": rule["level"],
                    "desc": _desc(rule, display_lang),
                    "examples": [],
                }
                by_key[rule["key"]] = entry
                order.append(rule["key"])
            if len(entry["examples"]) >= _EXAMPLE_CAP:
                continue
            if any(ex["sentence"] == sentence for ex in entry["examples"]):
                continue
            entry["examples"].append(
                {"sentence": sentence, "matches": [{"start": s, "end": e} for s, e in spans]}
            )
    return [by_key[k] for k in order]
