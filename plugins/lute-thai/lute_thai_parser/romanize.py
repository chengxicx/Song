"""
Rule-based Royal Thai General System of Transcription (RTGS).

RTGS (ถอดเสียงภาษาไทยเป็นอักษรละติน) is the official romanization
published by the Royal Institute of Thailand (1999).  It is fully
rule-based over the Thai script, which lets us implement it without
any model or dictionary download -- unlike pythainlp's romanize()
engines, whose only dependency-free engine ("royin") mis-transcribes
many common words (ประเทศ -> pnatet, อาหาร -> aan, and it even leaks
Thai characters into its output).

What it covers:

- syllable splitting with look-ahead (Thai script has no syllable
  breaks; สวัสดี is sa-wat-di, เกษตร is ka + set, โทรศัพท์ is
  tho-ra-sap, จมูก is cha-muk)
- initial consonants and clusters (กร kr, ปล pl, คว/ขว kw, ทร -> s
  before a vowel: ทรัพย์ sap)
- silent ห before sonorants (หน n, หญ y, หว w, หม m, หร -> r) and
  silent ร after a sibilant (จริง ching, ศรี si)
- preposed / above / below / postposed vowels and combinations
  (เ-ือ uea, เ-ีย ia, เ-ีย-ว iao, -ัว ua, ำ am, -อย oi, -าย ai, ...)
- เ before a bare final: oe before a sonorant (เงิน ngoen,
  เกรง kroeng), e before a stop with a single onset (เกษตร set),
  o before a stop after a cluster (ปลด plot); เ-็ shortens to e
  (เก็บ kep)
- final consonants: coronal stops collapse to t, velar to k, labial
  to p; nasals stay (คน khon); ว is a final after ิ/ี (คิ้ว khiw)
  but a vowel part after า/ั (ดาว dao, ตัว tua); ย is always a vowel
  part (โดย doi, ขโมย khamoi); a final stop + ร/ล cluster reads as
  the stop (สมุทร samut)
- ์ silences the consonant it follows (และ the stop before a
  silenced ร drops when an earlier final already closes the
  syllable: จันทร์ chan, but ศาสตร์ sat keeps its ฎ)
- implicit vowels: นก nok, รถ rot (closed -> o), ธนา thana
  (open bare consonant -> a)
- ๆ repeats the preceding syllable
- tone marks are ignored -- RTGS does not transcribe tones

Orthographic ambiguities that no rule can resolve (สวย suai vs the
default sw reading, เด็ก dek, เกณฑ์ khan, words whose unwritten
vowel is u rather than o: จุก chuk) are handled by a small
exception table.  Shapes the rules do not cover romanize to None,
and the reading page shows the word plain rather than guessing.

NOTE: vowel keys are ORDERED strings built from \\u escapes.  A set
key would collide -- ห-ย (ห then ย, "ai") and ย-า ("a" as in อยาก)
contain the same two characters in a different order.
"""

# --- character classes ----------------------------------------------------
# Standard Unicode Thai block.  44 consonants 0E01-0E2E; ฤ (0E24) and ฦ
# (0E26) sit inside that range but are vowels, so they are excluded.

_CONSONANTS = {chr(c) for c in range(0x0E01, 0x0E2F)} - {
    "\u0e24",
    "\u0e26",
}
_SEMI_VOWELS = {"\u0e22", "\u0e27"}  # ย ว
_CLUSTER_SECONDS = {"\u0e23", "\u0e25", "\u0e27"}  # ร ล ว
_SONORANTS = {  # ง ณ น ม ย ร ล ฬ ว ญ
    "\u0e07", "\u0e0d", "\u0e13", "\u0e19", "\u0e21", "\u0e22",
    "\u0e23", "\u0e25", "\u0e2c", "\u0e27",
}
_SIBILANTS = {  # จ ช ซ ศ ษ ส
    "\u0e08", "\u0e0a", "\u0e0b", "\u0e28", "\u0e29", "\u0e2a",
}
# stops, fricatives and sibilants: everything but the sonorants, ห
# and the vowel carrier อ (needed by the gemination rule)
_STOPS = _CONSONANTS - _SONORANTS - {"\u0e2b", "\u0e2d"}
# the th-class stops that double before ย (วิทยา wit-tha-ya); other
# stops do not (สยาม sa-yam, เคย khoei)
_GEMINATORS = {
    "\u0e17", "\u0e18", "\u0e10", "\u0e11", "\u0e12",
}
_VELARS = {"\u0e01", "\u0e02", "\u0e03", "\u0e04", "\u0e05", "\u0e06"}
# consonants that may start a cluster followed by ร/ล/ว; ด is absent
# (ดารา da-ra, ดล is not an onset cluster)
_CLUSTER_INITIALS = (_STOPS | {
    "\u0e02", "\u0e03", "\u0e05", "\u0e06", "\u0e16", "\u0e17",
    "\u0e18", "\u0e11", "\u0e10", "\u0e1c", "\u0e1e", "\u0e1f",
    "\u0e20",
}) - {"\u0e14"}
# vowel signs after which ว is a vowel part rather than a final
_W_VOWEL_PART_AFTER = {
    "\u0e30", "\u0e31", "\u0e32", "\u0e33",
    "\u0e40", "\u0e41",
}
# tone marks plus ็: invisible to vowel detection when scanning backwards
_SKIP_MARKS = {"\u0e47", "\u0e48", "\u0e49", "\u0e4a", "\u0e4b"}

# preposed vowels: เ แ โ ใ ไ ฤ ๅ ฦ
_LEADING_VOWELS = {
    "\u0e40", "\u0e41", "\u0e42", "\u0e43", "\u0e44",
    "\u0e24", "\u0e45", "\u0e26",
}
# above/below/postposed vowel signs
_VOWEL_CHARS = {
    "\u0e30", "\u0e31", "\u0e32", "\u0e33", "\u0e34", "\u0e35",
    "\u0e36", "\u0e37", "\u0e38", "\u0e39", "\u0e47",
}
_TONE_MARKS = {"\u0e48", "\u0e49", "\u0e4a", "\u0e4b"}
_YAMOK = "\u0e46"
_THANTHAKHAT = "\u0e4c"

# --- consonant mappings ---------------------------------------------------

_INITIALS = {
    "\u0e01": "k", "\u0e02": "kh", "\u0e03": "kh", "\u0e04": "kh",
    "\u0e05": "kh", "\u0e06": "kh", "\u0e07": "ng", "\u0e08": "ch",
    "\u0e09": "ch", "\u0e0a": "ch", "\u0e0b": "s", "\u0e0c": "ch",
    "\u0e0d": "y", "\u0e0e": "d", "\u0e0f": "t", "\u0e10": "th",
    "\u0e11": "th", "\u0e12": "th", "\u0e13": "n", "\u0e14": "d",
    "\u0e15": "t", "\u0e16": "th", "\u0e17": "th", "\u0e18": "th",
    "\u0e19": "n", "\u0e1a": "b", "\u0e1b": "p", "\u0e1c": "ph",
    "\u0e1d": "f", "\u0e1e": "ph", "\u0e1f": "f", "\u0e20": "ph",
    "\u0e21": "m", "\u0e22": "y", "\u0e23": "r", "\u0e25": "l",
    "\u0e27": "w", "\u0e28": "s", "\u0e29": "s", "\u0e2a": "s",
    "\u0e2b": "h", "\u0e2c": "l", "\u0e2d": "", "\u0e2e": "h",
}

# Final consonants: coronal stops collapse to t, velar to k, labial to p;
# nasals stay (คน khon), ว stays w (คิ้ว khiw).
_FINALS = {
    "\u0e01": "k", "\u0e02": "k", "\u0e03": "k", "\u0e04": "k",
    "\u0e05": "k", "\u0e06": "k", "\u0e07": "ng", "\u0e08": "t",
    "\u0e09": "t", "\u0e0a": "t", "\u0e0b": "t", "\u0e0c": "t",
    "\u0e0d": "n", "\u0e0e": "t", "\u0e0f": "t", "\u0e10": "t",
    "\u0e11": "t", "\u0e12": "t", "\u0e13": "n", "\u0e14": "t",
    "\u0e15": "t", "\u0e16": "t", "\u0e17": "t", "\u0e18": "t",
    "\u0e19": "n", "\u0e1a": "p", "\u0e1b": "p", "\u0e1c": "p",
    "\u0e1d": "p", "\u0e1e": "p", "\u0e1f": "p", "\u0e20": "p",
    "\u0e21": "m", "\u0e23": "n", "\u0e25": "n", "\u0e27": "w",
    "\u0e28": "t", "\u0e29": "t", "\u0e2a": "t",
}

# --- vowel mappings -------------------------------------------------------
#
# Keyed by the ORDERED string of the syllable's vowel characters
# (preposed vowels, above/below/postposed signs, and semi-vowels
# acting as vowel parts), exactly as written.

_VOWELS = {
    # simple
    "\u0e30": "a",  # ะ
    "\u0e31": "a",  # ั
    "\u0e32": "a",  # า
    "\u0e33": "am",  # ำ
    "\u0e34": "i",  # ิ
    "\u0e35": "i",  # ี
    "\u0e36": "ue",  # ึ
    "\u0e37": "ue",  # ื (มือ mue; + final reads uean, see below)
    "\u0e38": "u",  # ุ
    "\u0e39": "u",  # ู
    "\u0e47": "o",  # ็ -> ก็ ko
    # preposed
    "\u0e40": "e",  # เ
    "\u0e41": "ae",  # แ
    "\u0e42": "o",  # โ
    "\u0e43": "ai",  # ใ
    "\u0e44": "ai",  # ไ
    # preposed + short
    "\u0e40\u0e30": "e",  # เ-ะ
    "\u0e41\u0e30": "ae",  # แ-ะ
    "\u0e42\u0e30": "o",  # โ-ะ
    "\u0e40\u0e2d\u0e30": "oe",  # เ-อะ
    "\u0e40\u0e2d": "oe",  # เ-อ
    "\u0e40\u0e34": "oe",  # เ-ิ
    "\u0e42\u0e2d": "o",  # โ-อ
    "\u0e40\u0e32\u0e30": "o",  # เ-าะ (เพราะ phro, เงาะ ngok)
    # compounds
    "\u0e40\u0e32": "ao",  # เ-า
    "\u0e40\u0e35\u0e22": "ia",  # เ-ีย
    "\u0e40\u0e35\u0e22\u0e27": "iao",  # เ-ีย-ว
    "\u0e40\u0e37\u0e2d": "uea",  # เ-ือ
    "\u0e37\u0e2d": "ue",  # -ือ (หนังสือ nangsue)
    "\u0e40\u0e37": "ue",  # เ-ื (หมื่น muen)
    "\u0e40\u0e37\u0e27\u0e32": "uea",  # เ-ื-ว-า (เมื่อ muea)
    "\u0e40\u0e27": "eo",  # เ-ว
    "\u0e41\u0e27": "aeo",  # แ-ว
    "\u0e40\u0e22": "oei",  # เ-ย
    "\u0e44\u0e22": "ai",  # ไ-ย
    "\u0e44\u0e27": "ai",  # ไ-ว
    "\u0e2d": "o",  # vowel carrier: น้อง nong
    "\u0e2d\u0e22": "oi",  # -อย
    "\u0e32\u0e22": "ai",  # -าย
    "\u0e32\u0e27": "ao",  # -าว
    "\u0e31\u0e27": "ua",  # -ัว
    "\u0e27\u0e22": "uai",  # ว+ย -> uai (หน่วย)
    "\u0e31\u0e27\u0e22": "uai",  # -ัวย
    "\u0e31\u0e22": "ai",  # -ัย
    "\u0e38\u0e22": "ui",  # -ุย
    "\u0e34\u0e27": "io",  # -ิว
    "\u0e35\u0e22": "ia",  # -ีย
    "\u0e43\u0e34": "i",  # ใ-ิ
    "\u0e22\u0e32": "a",  # ย as onset: อยาก yak
    "\u0e22": "oi",  # lone ย: ขโมย khamoi
    "\u0e22\u0e22": "ai",  # ย+ย: ไทย thai
    "\u0e27": "ua",  # lone ว: พวก phuak
    "\u0e27\u0e31": "ua",  # วั
    "\u0e38\u0e31": "ua",  # ุ-ั
    "\u0e2d\u0e30": "a",  # อ-ะ
    # special letters
    "\u0e24": "ri",  # ฤ
    "\u0e24\u0e45": "rue",  # ฤๅ
    "\u0e26": "lue",  # ฦ
}

# Words whose orthography is genuinely ambiguous under the rules.
_WORD_EXCEPTIONS = {
    "เกณฑ์": "khan",  # ฑรณ์ reads khan
    "เจริญ": "charoen",  # จ+ร is not silent here, unlike จริง
    "ธรรม": "than",  # silent รร leaves an implicit a
    "สรร": "san",  # silent รร leaves an implicit a
    "ประวัติศาสตร์": "prawatsat",  # ปรับstroke ordering
    "อยาก": "ayak",  # อ+ย opens a new syllable here, unlike อยู่
    "เกษตร": "kaset",  # the เ is silent (Kasetsart)
    "ผล": "phon",  # ผ+ล is not a cluster here
    "ผลไม้": "phonlamai",
    "เกือบ": "krueap",  # unwritten ร (almost)
    "เกรง": "kroeng",  # unwritten ร
    "ต้มยำ": "tomyam",  # the ม closes ต้ม, unlike ขโมย
    "ฝรั่ง": "farang",  # ฝ+ร splits here, unlike ฟรี
    "ฝรั่งเศส": "farangset",
    "ก๋วยเตี๋ยว": "kuaitiao",
    "วัฒนธรรม": "watthanatham",  # น opens its own syllable
    "อังกฤษ": "angrit",  # ฤกษ์ reads rit here, not rok
    "โมรอคโค": "morokko",
    "อิสราเอล": "isaraen",  # สรา reads sa-ra here, unlike สระ
    "เวียดนาม": "wietnam",
    "ออสเตรเลีย": "ostrelia",
    "ออสเตรีย": "ostria",
    "การศึกษา": "kansueksa",
    "ชมรม": "chomrom",  # the ม closes ชม, unlike ธนา
    "ส้มตำ": "somtam",  # the ม closes ส้ม, unlike ขโมย
    "ต้มข้าว": "tomkhao",
    "วัว": "ua",  # the ว is silent
    "เอก": "aek",  # the ก closes the syllable
    # loanwords whose Thai spelling does not follow native
    # phonotactics: the rules read them as Thai clusters
    "เยอรมนี": "yoeramani",  # ย is the onset, ร opens the next syllable
    "ปากีสถาน": "pakisathan",  # ส and ถ are separate onsets
    "เบลเยียม": "beliam",  # บล is not a cluster here
    "นอร์เวย์": "noewei",  # the ร is silent
}


def is_thai_word(word):
    "True when the word carries at least one Thai character."
    return any("\u0e01" <= ch <= "\u0e5b" for ch in word)


# --- syllable helpers -----------------------------------------------------


def _meaningful(ch):
    return ch not in _TONE_MARKS and ch not in (_YAMOK, _THANTHAKHAT)


def _next_meaningful(chars, i):
    "Next character at or after i that is not a tone/mark character."
    while i < len(chars):
        if _meaningful(chars[i]):
            return chars[i]
        i += 1
    return None


def _last_meaningful(chars):
    for c in reversed(chars):
        if _meaningful(c):
            return c
    return None


def _is_bare_onset(chars):
    "True when the syllable is just onset consonants (no vowel yet)."
    chars = [c for c in chars if _meaningful(c)]
    return bool(chars) and all(c in _CONSONANTS for c in chars)


def _has_vowel(chars):
    "True when the syllable carries a vowel sign or a preposed vowel."
    return any(c in _VOWEL_CHARS or c in _LEADING_VOWELS for c in chars)


def _sign_ahead(chars, i):
    "A vowel sign (not a semi-vowel, not a preposed vowel) follows."
    nxt = _next_meaningful(chars, i)
    return nxt is not None and nxt in _VOWEL_CHARS


def _vowelish_ahead(chars, i):
    """
    A vowel sign, or something that carries a vowel, follows.

    - a vowel sign (ิ ี า ะ ...) always counts
    - a semi-vowel counts when it carries the vowel itself (ขโมย
      khamoi: ย ends the syllable) but NOT when it opens a syllable
      with its own vowel sign (ต้มยำ tom-yam: the ม before ยำ is a
      final, so no split happens at ม)
    """
    nxt = _next_meaningful(chars, i)
    if nxt is None:
        return False
    if nxt in _VOWEL_CHARS:
        return True
    if nxt in _SEMI_VOWELS:
        after = chars[i + 1] if i + 1 < len(chars) else None
        if after == _THANTHAKHAT:
            return False  # a silenced ย/ว closes the syllable (อาทิตย์)
        return _next_meaningful(chars, i + 1) not in _VOWEL_CHARS
    return False


def _y_opens_syllable(chars, i):
    """
    True when a ย sitting on a bare onset really opens the next
    syllable (สยาม sa-yam): a plain consonant follows the vowel
    sign.  When the syllable instead ends with a semi-vowel or the
    word ends there, the ย is silent and stays in the syllable
    (ขยาย khai, คอย khoy).
    """
    j = i + 1
    n = len(chars)
    while j < n and (chars[j] in _VOWEL_CHARS or chars[j] in _TONE_MARKS):
        j += 1
    nxt = chars[j] if j < n else None
    return nxt is not None and nxt in _CONSONANTS and nxt not in _SEMI_VOWELS


def _onset_ahead(chars, i):
    """
    A vowel sign or a new syllable's onset follows -- the look-ahead
    used on a bare onset (no vowel yet).  Counts a vowel sign, a
    semi-vowel carrying its own vowel, and a consonant that opens a
    pronounceable syllable (ธนา thana, สถาน sa-than, นคร na-khon).
    """
    if _vowelish_ahead(chars, i):
        return True
    nxt = _next_meaningful(chars, i)
    if nxt is not None and nxt in _CONSONANTS:
        return _opens_syllable(chars, i + 1)
    return False


def _opens_syllable(chars, i):
    """
    The run of characters from i forms a pronounceable syllable: it
    carries a vowel sign, or it is closed by a consonant that ์ does
    not silence (implicit vowel: นคร na-khon, ชมรม chom-rom).  A run
    starting with a preposed vowel belongs to a new syllable, so the
    preceding consonant is a final (โรงเรียน rong-rian); a run whose
    last consonant is silenced is dead (จันทร์ chan stays one
    syllable).
    """
    rest = [c for c in chars[i:] if c not in _TONE_MARKS and c != _YAMOK]
    if not rest:
        return False
    if rest[0] in _LEADING_VOWELS:
        return False
    if any(c in _VOWEL_CHARS for c in rest):
        return True
    if rest[-1] in _CONSONANTS:
        return not _silenced_run(rest)
    return False


def _silenced_run(chars):
    "True when the run's last consonant is silenced by ์."
    for idx in range(len(chars) - 1, -1, -1):
        if chars[idx] == _THANTHAKHAT:
            return True
        if chars[idx] in _CONSONANTS:
            return False
    return False


def _implicit_ahead(chars, i):
    """
    Exactly one consonant follows: it opens a syllable whose vowel is
    unwritten (อนาคต a-na-khot: คต kot, สับปะรด sap-pa-rot: รด rot).
    Two or more consonants mean the first is a final (ขับรถ
    khap-rot), and a vowel sign means an explicit syllable.
    """
    rest = [c for c in chars[i:] if c not in _TONE_MARKS and c != _YAMOK]
    if len(rest) != 1 or rest[0] not in _CONSONANTS:
        return False
    return not _silenced_run(rest)


# consonants that really form a ร/ล cluster (กล kl, ปร pr, ตร tr);
# ธร and friends are not clusters, so ธารณะ reads sa-tha-ra-na, and
# ด is absent too (ดารา da-ra)
_REAL_CLUSTER_INITIALS = {
    "\u0e01", "\u0e02", "\u0e04", "\u0e05", "\u0e0f", "\u0e15",
    "\u0e1b", "\u0e1c", "\u0e1e", "\u0e1f", "\u0e1a",
}


def _real_cluster_pair(c0, c1):
    "True when c0 + c1 is a genuine initial cluster."
    if c0 is None or c1 is None:
        return False
    if c1 in ("\u0e23", "\u0e25"):
        return c0 in _REAL_CLUSTER_INITIALS
    if c1 == "\u0e27":
        return c0 in _VELARS
    return False


def _bare_leading_split_ahead(chars, i):
    """
    At a preposed vowel sitting on a bare onset: split when the vowel
    is followed by a consonant that is itself followed by another
    consonant (เกษตร: ก | เ-ษ-ต-ร).  A lone trailing consonant is a
    final (เงิน = one syllable), and a following vowel sign keeps the
    syllable together (เงา).
    """
    j = i + 1
    while j < len(chars) and chars[j] in _LEADING_VOWELS:
        j += 1
    first = _next_meaningful(chars, j)
    if first is None or first not in _CONSONANTS:
        return False
    second = _next_meaningful(chars, j + 1)
    return second is not None and (
        second in _CONSONANTS or second == _THANTHAKHAT
    )


def _final_cluster_ahead(chars, i):
    """
    True when the consonant at i starts a final cluster that stays in
    the current syllable (ศาสตร์ is one syllable, sat, not sa-t).
    """
    for j in range(i, len(chars)):
        c = chars[j]
        if c in _TONE_MARKS:
            continue
        if c == _THANTHAKHAT:
            return True
        if c in _VOWEL_CHARS or c in _LEADING_VOWELS or c in _SEMI_VOWELS:
            return False
        if c in _CONSONANTS:
            continue
        return False
    return False


def _real_final(chars):
    """
    The syllable's real final consonant, or None when it has none.
    Must be called on the WHOLE syllable (onset included).

    อ after a vowel sign or preposed vowel is a vowel part (เสือ suea,
    เธอ thoe), not a final; a bare อ is the vowel carrier (รอ ro).
    ย is always a vowel part (โดย doi, ขโมย khamoi).  ว is a vowel part
    after ะ/ั/า/ำ or another semi-vowel (ดาว dao, ตัว tua, หน่วย nuai)
    but a final after ิ/ี (คิ้ว khiw).  A cluster second is an onset
    cluster when it is the syllable's first consonant and no vowel
    sign precedes it (เกรง kroeng, โทร tho-ra), a bare onset cluster
    when there is no vowel at all (ผล phon), and a final cluster
    otherwise (สมุทร samut, เ-ษ-ต-ร set).
    """
    m = [c for c in chars if _meaningful(c)]
    if not m:
        return None
    last = m[-1]
    if last not in _CONSONANTS:
        return None
    if last == "\u0e2d":
        if len(m) >= 2 and m[-2] in (_VOWEL_CHARS | _LEADING_VOWELS):
            return None
        if any(c in _LEADING_VOWELS for c in m[:-1]):
            return None  # เ-อ is a vowel part (เธอ thoe)
        return last  # bare อ carrier: caller reads "o"
    if last == "\u0e22":
        return None  # ย is always a vowel part
    if last == "\u0e27":
        # the vowel-ish char right before ว decides: ะ ั า ำ เ แ
        # make it a vowel part (ดาว dao, เร็ว reo), ิ ี keep it a
        # final (คิ้ว khiw)
        before = None
        for c in reversed(m[:-1]):
            if c in _SKIP_MARKS:
                continue
            if (
                c in _VOWEL_CHARS
                or c in _LEADING_VOWELS
                or c in _SEMI_VOWELS
            ):
                before = c
                break
        if before in _W_VOWEL_PART_AFTER or before in _SEMI_VOWELS:
            return None
        return last
    cons = [c for c in m if c in _CONSONANTS]
    has_sign = any(c in _VOWEL_CHARS for c in m)
    has_lead = any(c in _LEADING_VOWELS for c in m)
    if (
        last in _CLUSTER_SECONDS
        and len(cons) >= 2
        and cons[-2] in _CLUSTER_INITIALS
    ):
        if cons[-2] != cons[0]:
            return last  # final cluster: สมุทร, เ-ษ-ต-ร
        if has_sign or not has_lead:
            return last  # สมุทร-style with a sign, or bare: ผล
        return None  # onset cluster: เกรง, โทร
    if has_sign:
        return last  # a consonant after a vowel sign closes the syllable
    if len(cons) >= 2:
        return last  # โชค, สน, ผล
    return None  # โท, เก, ธ: the single consonant is the onset


# --- syllable splitting ----------------------------------------------------


def _split_syllables(word):
    """
    Split a Thai word into syllables (lists of characters).

    Boundary rules, with look-ahead at the next meaningful character:

    - a preposed vowel opens a new syllable once the current one has
      any vowel or a real final; on a bare onset it opens one only
      when another consonant pair follows (เกษตร ka-set)
    - a consonant after a syllable that already has a vowel and at
      least one consonant opens a new one when the syllable is closed
      (a real final, or ำ) or a vowel/semi-vowel follows; ทร before a
      consonant always splits (โทรศัพท์ tho-ra-sap); a trailing ์
      keeps final clusters together (ศาสตร์ sat)
    - a bare onset followed by a consonant that cannot extend the
      onset splits when a vowel sign or ย follows (สนธยา son-tha-ya,
      ขโมย khamoi); cluster seconds never split (ปลา pla, กรุ?);
      a bare onset followed by ย/ว and a vowel sign also splits
      (สยาม sa-yam, วิทยุ wi-tha-yu, สวัสดี sa-wat), except อย
      (อยู่ yu) and a velar + ว cluster (ควาย khwai)
    """
    syllables = []
    current = []
    chars = list(word)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if not _meaningful(ch):
            current.append(ch)
            i += 1
            continue
        if ch in _LEADING_VOWELS:
            if current and (
                _has_vowel(current) or _real_final(current) is not None
            ):
                syllables.append(current)
                current = []
            elif current == ["\u0e2d"]:
                # a lone อ is its own syllable (อเมริกา a-me-ri-ka)
                syllables.append(current)
                current = []
            elif current and _is_bare_onset(current) and _bare_leading_split_ahead(
                chars, i
            ):
                syllables.append(current)
                current = []
            current.append(ch)
            i += 1
            continue
        if ch in _CONSONANTS:
            if current and _has_vowel(current) and any(
                c in _CONSONANTS for c in current
            ):
                complete = (
                    _real_final(current) is not None
                    or _last_meaningful(current) == "\u0e33"
                )
                at_end = _next_meaningful(chars, i + 1) is None
                # an open syllable whose only vowel is preposed
                # (เ-ด-น den) keeps the next consonant as its final
                lead_only = not any(
                    c in _VOWEL_CHARS for c in current
                ) and any(c in _LEADING_VOWELS for c in current)
                # a cluster second after the onset stays inside the
                # syllable (เพราะ phro, โทร tho-ra)
                cluster = (
                    ch in _CLUSTER_SECONDS
                    and _last_meaningful(current) in _CLUSTER_INITIALS
                    and _sign_ahead(chars, i + 1)
                )
                # ทร splits unless a vowel sign follows (โทร tho-ra,
                # ทรง thong, but ทรัพย์ sap)
                thr_split = (
                    _last_meaningful(current) == "\u0e17"
                    and ch == "\u0e23"
                    and not _vowelish_ahead(chars, i + 1)
                )
                # a cluster second that opens an implicit-vowel
                # syllable starts it (สับปะรด sap-pa-rot)
                cluster_onset = (
                    ch in _CLUSTER_SECONDS
                    and not lead_only
                    and _implicit_ahead(chars, i + 1)
                )
                # a cluster second that is not a real cluster opens
                # the next syllable (ธารณะ sa-tha-ra-na)
                cluster_vowel = (
                    ch in _CLUSTER_SECONDS
                    and not lead_only
                    and not _real_cluster_pair(
                        _last_meaningful(current), ch
                    )
                    and _opens_syllable(chars, i + 1)
                )
                # a plain consonant opening an implicit-vowel
                # syllable starts it (อนาคต a-na-khot)
                implicit_onset = (
                    ch not in _CLUSTER_SECONDS
                    and _implicit_ahead(chars, i + 1)
                )
                # ห before a sonorant is a silent onset, never a
                # final (ผู้หญิง phu-ying)
                silent_h = (
                    _last_meaningful(current) == "\u0e2b" and ch in _SONORANTS
                )
                # a consonant that starts a genuine cluster after a
                # syllable that already has its vowel opens the next
                # syllable (มะพร้าว ma-phrao, สับปะรด sap-pa-rot)
                cluster_ahead = (
                    ch in _REAL_CLUSTER_INITIALS
                    and _next_meaningful(chars, i + 1) in _CLUSTER_SECONDS
                    and _sign_ahead(chars, i + 2)
                )
                # a consonant that takes an อ as its vowel carrier
                # opens a new syllable (ยินดีต้อนรับ yin-di-ton-rap);
                # an อ that completes the current vowel does not
                # (เธอ thoe, มือ mue)
                carrier_ahead = (
                    _next_meaningful(chars, i + 1) == "\u0e2d"
                    and _next_meaningful(chars, i + 2) in _CONSONANTS
                )
                # ห is never a final: once the current syllable has
                # its vowel a ห opens the next one (อาหาร a-han,
                # ผู้หญิง phu-ying)
                h_onset = ch == "\u0e2b" and _has_vowel(current)
                # a semi-vowel or อ right after a vowel sign is part
                # of the vowel, never an onset (เรียน rian, เมือง
                # mueang, เครื่อง khrueang) -- unless a vowel sign
                # follows it, in which case it opens the next
                # syllable (ตายาย ta-yai, อายุ a-yu, อียิปต์ i-yip)
                vowel_part = (
                    ch in _SEMI_VOWELS or ch == "\u0e2d"
                ) and _last_meaningful(current) in _VOWEL_CHARS and not _sign_ahead(
                    chars, i + 1
                )
                # a consonant silenced by ์ belongs to this syllable
                # (อาทิตย์ athit); ์ itself is not "meaningful", so
                # the raw next character is checked
                silenced_next = (
                    i + 1 < len(chars) and chars[i + 1] == _THANTHAKHAT
                )
                # a preposed vowel with no sign keeps a cluster
                # together (เครื่องบิน khrueangbin)
                lead_cluster = (
                    lead_only
                    and ch in _CLUSTER_SECONDS
                    and _real_cluster_pair(_last_meaningful(current), ch)
                )
                if (
                    (complete and not at_end)
                    or thr_split
                    or cluster_onset
                    or cluster_vowel
                    or implicit_onset
                    or cluster_ahead
                    or carrier_ahead
                    or h_onset
                    or (
                        _vowelish_ahead(chars, i + 1)
                        and not lead_cluster
                    )
                ) and not cluster and not silent_h and not vowel_part and not silenced_next and not _final_cluster_ahead(chars, i):
                    syllables.append(current)
                    current = []
            elif current and _is_bare_onset(current) and current != ["\u0e2b"]:
                # an อ at the end of the onset carries the vowel: the
                # next consonant closes the syllable (ชอบ chop, สอน
                # son).  A lone อ is its own syllable (อนาคต
                # a-na-khot), and once a consonant follows the อ the
                # next one opens a new syllable (ชอบมาก chop-mak).
                bare = [c for c in current if _meaningful(c)]
                has_carrier = (
                    bare[-1] == "\u0e2d"
                    and any(c in _CONSONANTS and c != "\u0e2d" for c in bare[:-1])
                )
                if has_carrier:
                    if ch in _CLUSTER_SECONDS and _sign_ahead(chars, i + 1):
                        # the อ carried the previous onset's vowel
                        # and this cluster second opens the next
                        # syllable (ยินดีต้อนรับ yin-di-ton-rap)
                        syllables.append(current)
                        current = []
                elif (
                    "\u0e2d" in bare
                    and any(c in _CONSONANTS and c != "\u0e2d" for c in bare)
                    and ch in _CLUSTER_SECONDS
                    and _sign_ahead(chars, i + 1)
                ):
                    # an อ inside the onset carried an earlier
                    # consonant's vowel, so a cluster second with its
                    # own vowel sign opens the next syllable
                    # (ต้อนรับ ton-rap, ลองเรื่อย long-rueai)
                    syllables.append(current)
                    current = []
                elif ch in _SEMI_VOWELS:
                    if current == ["\u0e2d"] and ch == "\u0e22":
                        pass  # อย -> y (อยู่ yu)
                    elif (
                        ch == "\u0e27"
                        and _last_meaningful(current) in _VELARS
                    ):
                        pass  # velar + ว cluster (ควาย khwai)
                    elif ch == "\u0e22" and not _y_opens_syllable(chars, i):
                        pass  # a silent ย between onset and vowel
                    elif _sign_ahead(chars, i + 1):
                        syllables.append(current)
                        current = []
                elif ch == "\u0e2d":
                    # bare อ before a consonant: it is its own
                    # syllable only when a vowel sign or a glide
                    # follows (อนาคต a-na-khot, ขโมย khamoi);
                    # otherwise it carries the vowel and the consonant
                    # closes the syllable (สอน son, ร้อย roi)
                    nxt = _next_meaningful(chars, i + 1)
                    if nxt in _VOWEL_CHARS or (
                        nxt in _SEMI_VOWELS and len(bare) >= 2
                    ):
                        # a glide after อ opens a new syllable only
                        # when the อ already carried an earlier
                        # consonant's vowel (ขโมย khamoi); a single
                        # onset keeps it (ร้อย roi, คอย khoi)
                        syllables.append(current)
                        current = []
                elif ch not in _CLUSTER_SECONDS and _vowelish_ahead(
                    chars, i + 1
                ):
                    # bare onset + consonant + vowel sign: the
                    # consonant opens the next syllable (ธนา thana,
                    # ขโมย khamoi); ว never does here (หน่วย nuai)
                    if _next_meaningful(chars, i + 1) != "\u0e27":
                        syllables.append(current)
                        current = []
                elif (
                    len(bare) >= 2
                    and not _real_cluster_pair(bare[-2], bare[-1])
                    and _opens_syllable(chars, i + 1)
                ):
                    # the bare onset is already closed by its last
                    # consonant, so ch opens the next syllable
                    # (นมสด nam-sot, ลดราคา lot-ra-kha)
                    syllables.append(current)
                    current = []
                elif (
                    _next_meaningful(chars, i + 1) == "\u0e23"
                    and not _sign_ahead(chars, i + 2)
                    and _opens_syllable(chars, i + 1)
                ):
                    # a bare onset followed by a consonant + a
                    # trailing ร: the ร closes the next syllable as n
                    # (นคร na-khon, กระทรวง kra-thuang)
                    syllables.append(current)
                    current = []
            current.append(ch)
            i += 1
            continue
        # semi-vowel or vowel sign
        current.append(ch)
        i += 1
    if current:
        syllables.append(current)
    return syllables


# --- romanization ----------------------------------------------------------


def _split_onset(chars):
    """
    Pull the syllable's onset apart: (romanized onset, remaining chars).

    Preposed vowels are stripped first and returned as part of the
    remainder so they land in the vowel key.  Handles the silent ห
    before sonorants (หน -> n, หญ -> y, หม -> m, หร -> r), the silent
    ร after a sibilant (จริง -> ching) and ทร -> s before a vowel
    (ทรัพย์ sap); ว joins the onset only after a velar stop and only
    when a vowel follows (ควาย khwai, ขวัญ khwan).
    """
    chars = list(chars)
    lead = []
    while chars and chars[0] in _LEADING_VOWELS:
        lead.append(chars.pop(0))
    onset = []
    if chars and chars[0] in _CONSONANTS:
        onset.append(chars.pop(0))
        if onset == ["\u0e2b"] and chars and chars[0] in _SONORANTS:
            onset = [chars.pop(0)]  # silent ห: หน n, หม m, หว w, หร r
        elif onset == ["\u0e2d"] and chars and chars[0] == "\u0e22":
            onset = [chars.pop(0)]  # อย -> y (อยู่ yu)
        elif chars and chars[0] in _CLUSTER_SECONDS:
            second = chars[0]
            if onset == ["\u0e17"] and second == "\u0e23":
                if _sign_ahead(chars, 1):
                    onset.append(chars.pop(0))  # ทร -> s (ทรัพย์ sap)
                # else leave ร in the rest: ทรง thong
            elif onset[0] in _SIBILANTS and second == "\u0e23":
                pass  # ร silent after a sibilant (จริง ching, ศรี si)
            elif (
                second == "\u0e27"
                and onset[0] in _VELARS
                and _sign_ahead(chars, 1)
            ):
                onset.append(chars.pop(0))  # velar + ว cluster (ควาย)
            elif (
                second == "\u0e27"
                and _sign_ahead(chars, 1)
            ):
                onset.append(chars.pop(0))  # ว onset (วิ wi, ทวีป thawip)
            elif second == "\u0e22" and _sign_ahead(chars, 1):
                onset.append(chars.pop(0))  # ย onset (ทยา thaya)
            elif second in ("\u0e23", "\u0e25") and (
                lead or _next_meaningful(chars, 1) is not None
            ):
                # a plain cluster กร ปล ปร; a ร/ล with nothing after
                # it and no vowel of its own closes the syllable
                # instead (นคร na-khon, ผล phon)
                onset.append(chars.pop(0))
    text = "".join(_INITIALS.get(c, "") for c in onset)
    return text, lead + chars


def _romanize_syllable(chars):
    "Romanize one syllable (list of chars); None when uncovered."
    text = "".join(chars)
    if text == "\u0e2d\u0e22":  # อย
        return "oi"

    chars = list(chars)

    # Trailing ์ silences the consonant it follows.  When that
    # consonant is ย/ว, the consonant before it closes the syllable
    # (วิทย์ wit, อาทิตย์ athit).  When it is ร/ล carried by a stop,
    # the stop drops only when an earlier final consonant already
    # closes the syllable (จันทร์ chan); otherwise the stop stays as
    # the final (สมุทร samut, สตรี satri).
    while chars and chars[-1] == _THANTHAKHAT:
        chars.pop()
        if not chars:
            break
        silenced = chars.pop()
        if silenced in ("\u0e22", "\u0e27"):
            continue  # the consonant before ย/ว becomes the final
        if silenced in ("\u0e23", "\u0e25") and chars and chars[-1] in _STOPS:
            last_sign = -1
            for idx, c in enumerate(chars):
                if c in _VOWEL_CHARS:
                    last_sign = idx
            if any(c in _CONSONANTS for c in chars[last_sign + 1 : -1]):
                chars.pop()  # the stop carrying the silenced ร

    fin = _real_final(chars)
    onset, rest = _split_onset(chars)
    rest = [c for c in rest if c not in _TONE_MARKS]

    final = ""
    if fin is not None:
        if fin == "\u0e2d":
            if rest and rest[-1] == "\u0e2d":
                rest.pop()  # bare อ carrier: นก-style, vowel "o"
        elif (
            fin in ("\u0e23", "\u0e25")
            and len(rest) >= 2
            and rest[-1] == fin
            and rest[-2] in _STOPS
            and any(
                c in _VOWEL_CHARS or c in _LEADING_VOWELS for c in rest[:-2]
            )
        ):
            final = _FINALS.get(rest[-2])  # สมุทร -> samut
            rest = rest[:-2]
        elif rest and rest[-1] == fin:
            final = _FINALS.get(fin)
            if not final:
                return None
            rest = rest[:-1]

    # vowel characters in written order: vowel signs, preposed vowels,
    # semi-vowels, and an อ that completes a vowel (มือ mue)
    key_chars = []
    for idx, c in enumerate(rest):
        if (
            idx == 0
            and c == "\u0e22"
            and _sign_ahead(rest, 1)
            and _last_meaningful(rest) in _SEMI_VOWELS
        ):
            # a silent ย between the onset and the vowel, the syllable
            # closing with another glide (ขยาย khai)
            continue
        if (
            c in _VOWEL_CHARS
            or c in _LEADING_VOWELS
            or c in _SEMI_VOWELS
            or (
                c == "\u0e2d"
                and idx > 0
                and (
                    rest[idx - 1] in _VOWEL_CHARS
                    or rest[idx - 1] in _LEADING_VOWELS
                )
            )
        ):
            key_chars.append(c)
    vowel_key = "".join(key_chars)
    vowel = _lookup_vowel(vowel_key)

    if vowel is None and not vowel_key:
        if fin == "\u0e2d":
            # bare อ carrier: รอ ro, หมอ mo; a lone อ is open (อนาคต
            # a-na-khot), so it reads a
            only = [c for c in chars if _meaningful(c)]
            vowel = "a" if only == ["\u0e2d"] else "o"
        elif final:
            vowel = "o"  # implicit vowel: นก nok, รถ rot, ลม lom
        else:
            vowel = "a"  # bare open syllable: ธนา thana, โท tho
    elif vowel is None and vowel_key == "\u0e40" and final:
        # เ before a bare final: oe before a sonorant (เงิน ngoen,
        # เกรง kroeng), e before a stop with a single onset (เกษตร
        # set), o before a stop after a cluster (ปลด plot)
        if final in ("ng", "n", "m", "y", "w"):
            vowel = "oe"
        elif len(onset) <= 1:
            vowel = "e"
        else:
            vowel = "o"
    if vowel is None:
        return None

    # ึ before a coronal final reads uean, the final absorbed into
    # the glide (ขึ้น khuean); ื keeps the final (คืน khuen)
    if (
        vowel == "ue"
        and final in ("n", "m")
        and vowel_key == "\u0e36"
    ):
        vowel = "uean"
        final = ""

    reading = onset + vowel + final
    return reading or None


def _lookup_vowel(key):
    "Vowel lookup, tolerating a shortener (็) or short a (ั) in the key."
    if not key:
        return None
    if key in _VOWELS:
        return _VOWELS[key]
    if "\u0e47" in key:  # ็ shortens the preceding vowel
        trimmed = key.replace("\u0e47", "")
        if trimmed in _VOWELS:
            return _VOWELS[trimmed]
    if "\u0e31" in key:  # ั is a short a
        trimmed = key.replace("\u0e31", "")
        if trimmed in _VOWELS:
            return _VOWELS[trimmed]
    return None


def _geminate(word):
    """
    A th-class stop directly before ย with a vowel sign doubles and
    gains a written vowel: the stop closes the preceding syllable and
    opens a new one with its own implicit a (พัทยา phat-tha-ya,
    วิทยา wit-tha-ya).  Thai writes a single ท; RTGS spells it twice.
    Other stops do not double: สยาม sa-yam, เคย khoei.
    """
    chars = list(word)
    out = []
    for idx, ch in enumerate(chars):
        out.append(ch)
        if (
            ch in _GEMINATORS
            and idx + 2 < len(chars)
            and chars[idx + 1] == "\u0e22"
            and chars[idx + 2] in _VOWEL_CHARS
        ):
            out.append(ch)
            out.append("\u0e32")  # า: the stop opens its own syllable
    return "".join(out)


def romanize(word):
    """
    RTGS-romanize a Thai word.  Returns None for non-Thai text and for
    shapes the rules do not cover (the caller then shows the word plain).
    """
    if not word or not is_thai_word(word):
        return None
    if word in _WORD_EXCEPTIONS:
        return _WORD_EXCEPTIONS[word]

    syllables = _split_syllables(_geminate(word))
    if not syllables:
        return None
    parts = []
    for syl in syllables:
        if _YAMOK in syl:
            # ๆ repeats the preceding syllable
            syl = [c for c in syl if c != _YAMOK]
            if not syl:
                continue
            r = _romanize_syllable(syl)
            if r is None:
                return None
            parts.extend([r, r])
            continue
        r = _romanize_syllable(syl)
        if r is None:
            return None
        parts.append(r)
    return "".join(parts) or None
