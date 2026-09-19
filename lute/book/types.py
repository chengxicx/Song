"""
The single registry of book types.

Every place that needs the set of book types reads from this one dict:

- Python: forms.py (edit-form Type dropdown, SUBTITLE_BOOK_TYPES),
  datatables.py (frontend filter whitelist), read/routes.py (subtitle
  books), book/routes.py (edit-page cue loading).
- Templates: base.html injects the registry as window.LUTE_BOOK_TYPES
  for the frontend (static/js/book-type-icons.js renders the listing
  chips and the import-type dropdown icons from it); read/index.html
  picks the player via book_media_types.
- Frontend JS: book-type-icons.js consumes window.LUTE_BOOK_TYPES; the
  import page builds its picker from the import_type entries.

Entry fields:

- label / short: display name (dropdowns) and chip label (listings).
- color: hex brand color for the icon chip.
- paths / filled: the icon SVG.  "filled" is a pre-styled inner-SVG
  string for brand marks; "paths" is stroked in book_type_icon_svg.
- stored: the key is a real value of books.BkBookType ("text" is the
  alias for the stored '' value).
- import_type: shown in the import page's type picker (webpage and epub
  imports are stored as '' -- plain text -- so they are not "stored").
- selectable: offered in the edit form's Type dropdown.
- subtitle: a subtitle book -- its reading text is generated from SRT
  cues and the player follows cue timings (forms.SUBTITLE_BOOK_TYPES).
- player: which reading-page player the type uses: "media" = the
  unified youtube-player (YouTube iframe or HTML5 audio/video backend),
  "bilibili" = the DASH bilibili player, None = no media player.

Dict order is the display order for the import picker and the Type
dropdown; keep new entries in the position they should appear.
"""

BOOK_TYPES = {
    "text": {
        "label": "Text",
        "short": "Text",
        "color": "#3b82f6",
        "paths": (
            '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
            '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": True,
        "subtitle": False,
        "player": None,
    },
    "webpage": {
        "label": "Web page",
        "short": "Web page",
        "color": "#0c8599",
        "paths": (
            '<circle cx="12" cy="12" r="10"/>'
            '<path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
            '<path d="M2 12h20"/>'
        ),
        "stored": False,
        "import_type": True,
        "selectable": False,
        "subtitle": False,
        "player": None,
    },
    "youtube": {
        "label": "YouTube video",
        "short": "YouTube",
        "color": "#FF0000",
        "filled": (
            '<path fill="#FF0000" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545'
            "s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0"
            " 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93"
            ' 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": True,
        "subtitle": True,
        "player": "media",
    },
    "bilibili": {
        "label": "Bilibili video",
        "short": "Bilibili",
        "color": "#00A1D6",
        "filled": (
            '<path d="M8.2 2.2 11 6M15.8 2.2 13 6" stroke="#00A1D6" stroke-width="2" '
            'stroke-linecap="round" fill="none"/>'
            '<rect x="3" y="6.6" width="18" height="13.8" rx="3.2" fill="#00A1D6"/>'
            '<circle cx="9" cy="12.4" r="1.7" fill="#fff"/>'
            '<circle cx="15" cy="12.4" r="1.7" fill="#fff"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": True,
        "subtitle": True,
        "player": "bilibili",
    },
    "mp3": {
        "label": "MP3 / M4A audio",
        "short": "MP3",
        "color": "#2f9e44",
        "paths": (
            '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7'
            'a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": True,
        "subtitle": True,
        "player": "media",
    },
    "netease": {
        "label": "NetEase Cloud Music",
        "short": "NetEase",
        "color": "#C20C0C",
        "paths": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
        "stored": True,
        "import_type": True,
        "selectable": True,
        "subtitle": True,
        "player": "media",
    },
    "video": {
        "label": "Online video",
        "short": "Video",
        "color": "#845ef7",
        "paths": (
            '<path d="M10 7.75a.75.75 0 0 1 1.142-.638l3.664 2.249a.75.75 0 0 1 0 1.278l-3.664 2.25'
            'a.75.75 0 0 1-1.142-.64z"/>'
            '<path d="M12 17v4"/><path d="M8 21h8"/>'
            '<rect x="2" y="3" width="20" height="14" rx="2"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": True,
        "subtitle": True,
        "player": "media",
    },
    "manga": {
        "label": "Mokuro Manga",
        "short": "Manga",
        "color": "#f76707",
        "paths": (
            '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>'
            '<circle cx="9" cy="9" r="2"/>'
            '<path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": False,
        "subtitle": False,
        "player": None,
    },
    "pdf": {
        "label": "PDF",
        "short": "PDF",
        "color": "#e03131",
        "paths": (
            '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
            '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
            '<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
        ),
        "stored": True,
        "import_type": True,
        "selectable": False,
        "subtitle": False,
        "player": None,
    },
    "epub": {
        "label": "EPUB",
        "short": "EPUB",
        "color": "#d6336c",
        "paths": (
            '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>'
        ),
        "stored": False,
        "import_type": True,
        "selectable": False,
        "subtitle": False,
        "player": None,
    },
    # Not a book: the alias the series-aggregation branch reports for
    # aggregate rows in the listings, so they get the stack icon.
    "series": {
        "label": "Book Sets",
        "short": "Sets",
        "color": "#4c6ef5",
        "paths": (
            '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91'
            'a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/>'
            '<path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>'
            '<path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>'
        ),
        "stored": False,
        "import_type": False,
        "selectable": False,
        "subtitle": False,
        "player": None,
    },
}


def subtitle_book_types():
    "Book types whose text is generated from subtitles (SRT cues)."
    return tuple(k for k, v in BOOK_TYPES.items() if v["subtitle"])


def media_player_types():
    'Book types played by the unified media player (player == "media").'
    return tuple(k for k, v in BOOK_TYPES.items() if v["player"] == "media")


def selectable_type_choices():
    "(form value, label) pairs for the edit form's Type dropdown."
    return tuple(
        (("" if k == "text" else k), v["label"])
        for k, v in BOOK_TYPES.items()
        if v["selectable"]
    )


def import_type_choices():
    "{value, label} dicts for the import page's type picker, in order."
    return [
        {"value": k, "label": v["label"]}
        for k, v in BOOK_TYPES.items()
        if v["import_type"]
    ]


def stored_book_type_filter_values():
    """
    Book type values the frontend listings may filter by.  "text" is the
    alias for the stored '' value, "series" the aggregate-row alias.
    """
    return (
        ("", "text")
        + tuple(k for k, v in BOOK_TYPES.items() if v["stored"] and k != "text")
        + ("series",)
    )
