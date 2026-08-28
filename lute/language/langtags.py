"""
BCP-47 language tags offered in the language form dropdowns.

Options are "tag + description" pairs, sourced from
https://gist.github.com/typpo/b2b828a35e683b9bf8db91b5404f1bd1
(the browser voice / Web Speech API tag list).  These are the tags
that TTS voices are keyed under, and they are also accepted by the
Google translate endpoints.
"""

LANGUAGE_TAGS = [
    ("ar-SA", "Arabic (Saudi Arabia)"),
    ("bn-BD", "Bangla (Bangladesh)"),
    ("bn-IN", "Bangla (India)"),
    ("cs-CZ", "Czech (Czech Republic)"),
    ("da-DK", "Danish (Denmark)"),
    ("de-AT", "Austrian German"),
    ("de-CH", '"Swiss" German'),
    ("de-DE", "Standard German (as spoken in Germany)"),
    ("el-GR", "Modern Greek"),
    ("en-AU", "Australian English"),
    ("en-CA", "Canadian English"),
    ("en-GB", "British English"),
    ("en-IE", "Irish English"),
    ("en-IN", "Indian English"),
    ("en-NZ", "New Zealand English"),
    ("en-US", "US English"),
    ("en-ZA", "English (South Africa)"),
    ("es-AR", "Argentine Spanish"),
    ("es-CL", "Chilean Spanish"),
    ("es-CO", "Colombian Spanish"),
    ("es-ES", "Castilian Spanish (as spoken in Central-Northern Spain)"),
    ("es-MX", "Mexican Spanish"),
    ("es-US", "American Spanish"),
    ("fi-FI", "Finnish (Finland)"),
    ("fr-BE", "Belgian French"),
    ("fr-CA", "Canadian French"),
    ("fr-CH", '"Swiss" French'),
    ("fr-FR", "Standard French (especially in France)"),
    ("he-IL", "Hebrew (Israel)"),
    ("hi-IN", "Hindi (India)"),
    ("hu-HU", "Hungarian (Hungary)"),
    ("id-ID", "Indonesian (Indonesia)"),
    ("it-CH", '"Swiss" Italian'),
    ("it-IT", "Standard Italian (as spoken in Italy)"),
    ("ja-JP", "Japanese (Japan)"),
    ("ko-KR", "Korean (Republic of Korea)"),
    ("nl-BE", "Belgian Dutch"),
    ("nl-NL", "Standard Dutch (as spoken in The Netherlands)"),
    ("no-NO", "Norwegian (Norway)"),
    ("pl-PL", "Polish (Poland)"),
    ("pt-BR", "Brazilian Portuguese"),
    ("pt-PT", "European Portuguese (as written and spoken in Portugal)"),
    ("ro-RO", "Romanian (Romania)"),
    ("ru-RU", "Russian (Russian Federation)"),
    ("sk-SK", "Slovak (Slovakia)"),
    ("sv-SE", "Swedish (Sweden)"),
    ("ta-IN", "Indian Tamil"),
    ("ta-LK", "Sri Lankan Tamil"),
    ("th-TH", "Thai (Thailand)"),
    ("tr-TR", "Turkish (Turkey)"),
    ("zh-CN", "Mainland China, simplified characters"),
    ("zh-HK", "Hong Kong, traditional characters"),
    ("zh-TW", "Taiwan, traditional characters"),
]


def tag_choices(default_description):
    """
    Dropdown choices for a language tag field: a leading Default
    option (describing the fallback behaviour) followed by every
    known tag as "tag -- description".
    """
    return [("", f"Default ({default_description})")] + [
        (tag, f"{tag} -- {description}") for tag, description in LANGUAGE_TAGS
    ]
