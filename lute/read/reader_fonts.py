"""
Font choices for the reading pane (the sidebar's "Font" picker).

The reading text's font-family is baked into each theme stylesheet --
sometimes with !important, e.g. `div#thetext p { font-family: ... }` --
so a reader's choice cannot be applied by inheritance.  It is applied
instead as an override anchored on #thetext (see the "Reader font
choice" block in static/css/styles.css): an id + attribute selector
beats the theme rules, and its !important beats a theme's !important
because it is more specific.  text-options.js sets the pick, stores it
in localStorage and stamps it onto #thetext.

Each choice is a complete CSS font stack, not a single family: the
device renders with the first family it actually has, so the same
choice works on macOS (Hiragino / PingFang), Windows (Yu / YaHei / MS)
and Linux / Android (Noto).  Nothing is shipped -- an absent family
just falls through to the next one.

The list is per language rather than one global set: what "a serif
font" means differs by script (Mincho for Japanese, Songti for Chinese,
Georgia for Latin, Geeza Pro for Arabic), and a Latin stack in front of
CJK text only produces the browser's default CJK fallback -- i.e. the
same thing the theme already shows.  Languages with no list of their
own share LATIN_FONTS, which is the sensible default for any script
that borrows Latin punctuation and digits.
"""

# The empty key means "leave the theme's own font alone"; it is always
# the first choice, so a reader who never touches the picker sees
# exactly the typography the theme author intended.
THEME_DEFAULT = {"key": "", "label": "Theme default", "stack": ""}


def _choice(key, label, stack):
    "One picker entry.  label is an i18n key (English source string)."
    return {"key": key, "label": label, "stack": stack}


# Japanese: the three families a Japanese reader actually distinguishes.
# Hiragino (macOS/iOS) first, then Yu / Meiryo (Windows), then Noto
# (Android / Linux).
JA_FONTS = [
    _choice(
        "mincho",
        "Mincho",
        '"Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", '
        '"Source Han Serif JP", "MS Mincho", serif',
    ),
    _choice(
        "gothic",
        "Gothic",
        '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", '
        'Meiryo, "Noto Sans JP", sans-serif',
    ),
    _choice(
        "maru",
        "Round Gothic",
        '"Hiragino Maru Gothic ProN", "M PLUS Rounded 1c", '
        '"Yu Gothic UI", sans-serif',
    ),
]

# Chinese / Cantonese.  The traditional Songti/Kaiti/Yuanti faces are
# listed before the PRC Simplified equivalents, but both are in the
# stack so the same choice looks native on either side.
ZH_FONTS = [
    _choice(
        "songti",
        "Songti",
        '"Songti SC", STSong, SimSun, "Noto Serif CJK SC", '
        '"Source Han Serif SC", serif',
    ),
    _choice(
        "heiti",
        "Heiti",
        '"PingFang SC", "Heiti SC", "Microsoft YaHei", '
        '"Noto Sans CJK SC", sans-serif',
    ),
    _choice(
        "kaiti",
        "Kaiti",
        '"Kaiti SC", STKaiti, KaiTi, BiauKai, "Noto Serif CJK SC", serif',
    ),
    _choice(
        "yuanti",
        "Yuanti",
        '"Yuanti SC", STYuanti, "PingFang SC", "Microsoft YaHei", sans-serif',
    ),
]

# Korean: Myeongjo (serif) vs Gothic (sans).  Batang is the Windows
# serif name, Nanum / Noto cover Linux and Android.
KO_FONTS = [
    _choice(
        "myeongjo",
        "Myeongjo",
        'AppleMyungjo, "Nanum Myeongjo", Batang, "Noto Serif KR", serif',
    ),
    _choice(
        "gothic",
        "Gothic",
        '"Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif',
    ),
]

# Arabic: the Naskh book face vs a modern sans.  Geeza Pro ships with
# macOS, Noto with everything else.
AR_FONTS = [
    _choice(
        "naskh",
        "Naskh",
        '"Geeza Pro", "Noto Naskh Arabic", "Traditional Arabic", serif',
    ),
    _choice(
        "sans",
        "Sans-serif",
        '"Noto Sans Arabic", "Geeza Pro", "Segoe UI", sans-serif',
    ),
]

# Thai: the looped book faces (Ayuthaya / Sathu) vs the screen sans
# (Thonburi, which is also the iOS system face).
TH_FONTS = [
    _choice("serif", "Serif", 'Ayuthaya, "Noto Serif Thai", Sathu, serif'),
    _choice("sans", "Sans-serif", 'Thonburi, "Noto Sans Thai", Tahoma, sans-serif'),
]

# Latin script: the classic pair.  Used for every language without a
# list of its own -- English, Spanish, French, German, Italian,
# Portuguese, Russian (Georgia carries Cyrillic) and any user-defined
# language.
LATIN_FONTS = [
    _choice("serif", "Serif", 'Georgia, "Times New Roman", "Noto Serif", serif'),
    _choice(
        "sans",
        "Sans-serif",
        '"Helvetica Neue", Helvetica, Arial, "Segoe UI", "Noto Sans", sans-serif',
    ),
]

FONTS_BY_LANG = {
    "ja": JA_FONTS,
    "zh": ZH_FONTS,
    # Cantonese is written with the same Han characters as Chinese, so
    # it uses the same faces (the reader's conventional names for them
    # are the same).
    "yue": ZH_FONTS,
    "ko": KO_FONTS,
    "ar": AR_FONTS,
    "th": TH_FONTS,
}


def font_choices(lang_code):
    """
    The picker's entries for a BCP-47 language tag.

    The tag is reduced to its primary subtag, so a custom tts_lang like
    "pt-BR" or "zh-Hant" still lands on the right list.  Unknown
    languages get LATIN_FONTS rather than an empty picker: every script
    renders something in Georgia/Helvetica, and the reader can always
    pick "theme default".
    """
    code = (lang_code or "").split("-")[0].strip().lower()
    fonts = FONTS_BY_LANG.get(code, LATIN_FONTS)
    # A fresh list per call: callers (tests, the template) must not be
    # able to mutate the module-level tables through it.
    return [THEME_DEFAULT, *fonts]
