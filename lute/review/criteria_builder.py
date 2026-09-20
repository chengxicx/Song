"""
Structured front-end for the review-spec criteria DSL.

The stored value of a spec is still a plain criteria string (see
lute.ankiexport.criteria); this module is the bridge between that
string and the dropdown-driven builder the UI uses:

    build_criteria(joiner, rows)  -> 'status >= 2 and tags:["vocab"]'
    parse_criteria('status >= 2') -> {'joiner': 'and', 'rows': [...]}

parse_criteria returns None for anything the flat builder cannot
represent (mixed and/or, or unrecognised syntax), which is the signal
for the page to fall back to the raw-textarea view -- so hand-written
criteria are never silently mangled.

The DSL has no parentheses and joins with and/or, so a top-level split
outside of quotes and brackets is exact.
"""

import re

from sqlalchemy import text

from lute.models.term import TermTag
from lute.stats.service import STATUS_LABELS

# Operators the DSL understands for the numeric fields.  Ordered by
# usefulness: the first is what a new row starts on.  '<>' is accepted
# by the parser as an alias of '!=' (see criteria.py).
_COMPARISON_OPS = [">=", "<=", "==", "!=", ">", "<"]

# How the UI should render a field's value control.
VALUE_STATUS = "status"
VALUE_LANGUAGE = "language"
VALUE_TAG = "tag"
VALUE_INT = "int"
VALUE_HAS = "has"

# One entry per selectable field.  'ops' is ordered: the first is the
# default the builder picks for a new row.
FIELD_SPECS = [
    {
        "name": "status",
        "label": "Status",
        "ops": _COMPARISON_OPS,
        "value_kind": VALUE_STATUS,
    },
    {
        "name": "language",
        "label": "Language",
        "ops": ["==", "!="],
        "value_kind": VALUE_LANGUAGE,
    },
    {
        "name": "tags",
        "label": "Term has tag",
        "ops": [":"],
        "value_kind": VALUE_TAG,
    },
    {
        "name": "parents.tags",
        "label": "Parent has tag",
        "ops": [":"],
        "value_kind": VALUE_TAG,
    },
    {
        "name": "all.tags",
        "label": "Term or parent has tag",
        "ops": [":"],
        "value_kind": VALUE_TAG,
    },
    {
        "name": "parents.count",
        "label": "Number of parents",
        "ops": _COMPARISON_OPS,
        "value_kind": VALUE_INT,
    },
    {
        "name": "has",
        "label": "Has",
        "ops": [":"],
        "value_kind": VALUE_HAS,
    },
]

_TAG_FIELDS = ("tags", "parents.tags", "all.tags")
_NUMERIC_FIELDS = ("status", "parents.count")

_HAS_OPTIONS = ["image"]

_OP_LABELS = {
    "<": "less than",
    "<=": "at most",
    ">": "more than",
    ">=": "at least",
    "==": "is",
    "!=": "is not",
    ":": "has",
}

# Presets shown in the 'Start from' dropdown, in order.  Language and
# tag presets are appended from the user's own data.
_BASE_PRESETS = [
    ("Common", "all", "All learning terms", "and", []),
    (
        "Common",
        "learning",
        "Learning levels 1-5",
        "and",
        [("status", ">=", ["1"]), ("status", "<=", ["5"])],
    ),
    ("Common", "lv2", "Level 2 and above", "and", [("status", ">=", ["2"])]),
    ("Common", "lv3", "Level 3 and above", "and", [("status", ">=", ["3"])]),
    ("Common", "shaky", "Level 2 or lower", "and", [("status", "<=", ["2"])]),
    ("Common", "mastered", "Well-known only", "and", [("status", "==", ["99"])]),
    ("Common", "image", "Has an image", "and", [("has", ":", ["image"])]),
    (
        "Common",
        "phrases",
        "Has a parent term",
        "and",
        [("parents.count", ">=", ["1"])],
    ),
]

# The preset a brand-new spec starts from.
DEFAULT_PRESET_ID = "learning"

# Cap on the auto-generated per-tag presets, so the dropdown stays sane
# in a library with hundreds of tags.
_MAX_TAG_PRESETS = 15


def field_spec(field_name):
    "FIELD_SPECS entry for a field name, or None."
    for spec in FIELD_SPECS:
        if spec["name"] == field_name:
            return spec
    return None


def _row(field, op, values):
    "Build one builder row."
    return {"field": field, "op": op, "values": [str(v) for v in values]}


def _clean_scalar(values):
    "First non-blank value of a row, or None."
    for v in values or []:
        v = str(v).strip()
        if v:
            return v
    return None


def _quote_tag(tag):
    "Quote a tag for the DSL, dropping embedded quotes."
    return '"' + str(tag).replace('"', "").strip() + '"'


def _part_for_row(row):
    """
    One row -> one criteria clause, or '' if the row is incomplete.

    Incomplete rows are skipped rather than raising: the builder emits
    a clause on every keystroke, so half-filled rows are normal.
    """
    field = (row or {}).get("field")
    spec = field_spec(field)
    if spec is None:
        return ""
    op = (row.get("op") or "").strip()
    # The parser's aliases normalize to the canonical spelling.
    op = {"=": "==", "<>": "!="}.get(op, op)
    if op not in spec["ops"]:
        op = spec["ops"][0]
    values = row.get("values") or []

    if field in _NUMERIC_FIELDS:
        val = _clean_scalar(values)
        if val is None or not re.fullmatch(r"-?\d+", val):
            return ""
        return f"{field} {op} {int(val)}"

    if field == "language":
        val = _clean_scalar(values)
        if not val:
            return ""
        return f"language {op} {_quote_tag(val)}"

    if field in _TAG_FIELDS:
        tags = [str(v).strip() for v in values if str(v).strip()]
        # De-dupe, order preserved.
        tags = list(dict.fromkeys(tags))
        if not tags:
            return ""
        if len(tags) == 1:
            return f"{field}:{_quote_tag(tags[0])}"
        return f"{field}:[{', '.join(_quote_tag(t) for t in tags)}]"

    if field == "has":
        val = _clean_scalar(values)
        if not val:
            return ""
        return f"has:{val}"

    return ""


def build_criteria(joiner, rows):
    "Serialize builder rows back to a criteria string."
    joiner = "or" if str(joiner).strip().lower() == "or" else "and"
    parts = [p for p in (_part_for_row(r) for r in rows or []) if p]
    return f" {joiner} ".join(parts)


def _split_top_level(s):
    """
    Split a criteria string on ' and ' / ' or ' outside quotes and
    brackets.  Returns (joiner, parts), or None when both joiners are
    used (the flat builder can only express one).
    """
    parts = []
    joiners = set()
    buf = []
    in_quote = False
    in_bracket = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            i += 1
            continue
        if not in_quote:
            if ch == "[":
                in_bracket = True
            elif ch == "]":
                in_bracket = False
            if not in_bracket:
                matched = None
                for kw in (" and ", " or "):
                    if s.startswith(kw, i):
                        matched = kw.strip()
                        break
                if matched is not None:
                    parts.append("".join(buf).strip())
                    joiners.add(matched)
                    buf = []
                    i += len(matched) + 2
                    continue
        buf.append(ch)
        i += 1
    if in_quote or in_bracket:
        return None
    parts.append("".join(buf).strip())
    if len(joiners) > 1:
        return None
    return (joiners.pop() if joiners else "and"), parts


_NUMERIC_RE = re.compile(
    r"^(status|parents\.count)\s*(<=|>=|<>|!=|==|=|<|>)\s*(-?\d+)$"
)
_LANGUAGE_RE = re.compile(r'^language\s*(:|==|=|!=)\s*"([^"]*)"$')
_TAG_RE = re.compile(r"^(tags|parents\.tags|all\.tags)\s*:\s*(.+)$")
_HAS_RE = re.compile(r"^has\s*:\s*(\w+)$")


def _row_for_part(part):
    "One criteria clause -> a builder row, or None if unrecognised."
    part = part.strip()
    if not part:
        return None

    m = _NUMERIC_RE.match(part)
    if m:
        field, op, val = m.group(1), m.group(2), m.group(3)
        if op in ("=",):
            op = "=="
        if op == "<>":
            op = "!="
        return _row(field, op, [val])

    m = _LANGUAGE_RE.match(part)
    if m:
        op = "==" if m.group(1) in (":", "=", "==") else "!="
        return _row("language", op, [m.group(2)])

    m = _TAG_RE.match(part)
    if m:
        tags = re.findall(r'"([^"]*)"', m.group(2))
        if not tags:
            return None
        return _row(m.group(1), ":", tags)

    m = _HAS_RE.match(part)
    if m:
        if m.group(1) not in _HAS_OPTIONS:
            return None
        return _row("has", ":", [m.group(1)])

    return None


def parse_criteria(criteria):
    """
    Criteria string -> {'joiner', 'rows'}, or None when it cannot be
    shown in the builder (caller falls back to the raw textarea).
    """
    s = (criteria or "").strip()
    if s == "":
        return {"joiner": "and", "rows": []}
    split = _split_top_level(s)
    if split is None:
        return None
    joiner, parts = split
    rows = []
    for part in parts:
        row = _row_for_part(part)
        if row is None:
            return None
        rows.append(row)
    return {"joiner": joiner, "rows": rows}


def _status_options(session):
    "Status id/name pairs from the statuses table, with a static fallback."
    try:
        rows = session.execute(text("select StID, StText from statuses")).all()
    except Exception:  # pylint: disable=broad-exception-caught
        rows = []
    pairs = [(int(r[0]), r[1]) for r in rows] or sorted(STATUS_LABELS.items())
    return [{"value": str(v), "label": f"{v} - {name}"} for v, name in pairs]


def _tag_options(session):
    "Distinct term tags, alphabetical."
    rows = session.query(TermTag.text).distinct().order_by(TermTag.text).all()
    return [r[0] for r in rows if r[0]]


def default_criteria():
    "The criteria a brand-new spec starts from."
    for _group, pid, _label, joiner, rowdefs in _BASE_PRESETS:
        if pid == DEFAULT_PRESET_ID:
            return build_criteria(joiner, [_row(f, o, v) for f, o, v in rowdefs])
    return ""


def presets(language_names, tag_names):
    "The 'Start from' dropdown: (group, id, label, joiner, rows) tuples."
    out = [
        {
            "group": group,
            "id": pid,
            "label": label,
            "joiner": joiner,
            "rows": [_row(f, o, v) for f, o, v in rowdefs],
        }
        for group, pid, label, joiner, rowdefs in _BASE_PRESETS
    ]
    out.extend(
        {
            "group": "By language",
            "id": f"lang:{name}",
            "label": name,
            "joiner": "and",
            "rows": [_row("language", "==", [name])],
        }
        for name in language_names
    )
    out.extend(
        {
            "group": "By tag",
            "id": f"tag:{tag}",
            "label": tag,
            "joiner": "and",
            "rows": [_row("tags", ":", [tag])],
        }
        for tag in tag_names[:_MAX_TAG_PRESETS]
    )
    for p in out:
        p["criteria"] = build_criteria(p["joiner"], p["rows"])
    return out


def builder_meta(session, language_names):
    """
    Everything the criteria builder needs, as plain JSON-able data.

    The routes pass this to the template; the JS reads it from a JSON
    script tag, so no template logic has to know about fields or ops.

    There is deliberately no "default preset" key.  The builder names
    the preset by matching the serialized criteria against each option's
    own criteria, which is the only thing that is also correct on the
    edit page, where the spec's criteria need not be the default.  One
    used to be sent here; the JS never read it, and a value that is
    written but never read is how the preset dropdown came to look
    broken in the first place.
    """
    tag_names = _tag_options(session)
    return {
        "fields": FIELD_SPECS,
        "op_labels": _OP_LABELS,
        "statuses": _status_options(session),
        "languages": list(language_names),
        "tags": tag_names,
        "has_options": _HAS_OPTIONS,
        "presets": presets(language_names, tag_names),
    }
