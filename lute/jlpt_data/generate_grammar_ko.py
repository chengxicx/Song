"""
Generate lute/jlpt_data/grammar_ko.json from a local checkout of
kimchi-grammar (https://github.com/Alaanor/kimchi-grammar, CC-BY 4.0).

The generator pulls the `name`, per-definition English `meaning`,
example sentences and their translations from each point/*.yaml, strips
Kimchi's custom markdown (<f>…</f>, :grammar, :dict, ::example, headers),
and emits one flat record per definition.

For each definition it also stores a `focus` list: the literal phrases
that the Kimchi authors wrapped in <f>…</f> inside that definition's
example sentences.  These mark exactly the grammar morphemes, so the
Korean grammar engine can use them (as literal substrings) to recognise
the construction on a reading page without an extra hand-written rule.

Usage:
    python lute/jlpt_data/generate_grammar_ko.py <kimchi-point-dir> \
        [out.json]
"""

import json
import os
import re
import sys

import yaml

_TAG_RE = re.compile(r"<[^>]+>")
_F_RE = re.compile(r"<f>(.*?)</f>", re.S)


def _clean(text):
    "Strip Kimchi html/markup tags from a text field."
    if not text:
        return ""
    return _TAG_RE.sub("", text).strip()


def _focus_phrases(sentence):
    "Literal strings wrapped in <f>…</f> inside an example sentence."
    return [p.strip() for p in _F_RE.findall(sentence or "") if p.strip()]


def _entry(key, name, defi, etype):
    focus = []
    examples = []
    for ex in defi.get("examples") or []:
        sentence = ex.get("sentence") or ""
        examples.append(_clean(sentence))
        focus.extend(_focus_phrases(sentence))
    # Longest-first so the engine can prefer the most specific literal.
    focus = sorted(set(focus), key=len, reverse=True)
    return {
        "key": key,
        "name": name,
        "slug": defi.get("slug", ""),
        "meaning": (defi.get("meaning") or "").strip(),
        "type": etype,
        "examples": examples,
        "focus": focus,
    }


def generate(point_dir):
    entries = []
    for fname in sorted(os.listdir(point_dir)):
        if not fname.endswith(".yaml"):
            continue
        path = os.path.join(point_dir, fname)
        stem = fname[:-5]
        try:
            data = yaml.safe_load(open(path, encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-except
            print("skip unparsable:", fname, exc)
            continue
        if not data:
            continue
        name = data.get("name", "")
        etype = (data.get("metadata") or {}).get("type", "")
        defs = data.get("definitions") or []
        if not defs:
            defs = [{"slug": "", "meaning": "", "examples": []}]
        for idx, defi in enumerate(defs):
            slug = (defi.get("slug") or "").strip()
            suffix = slug or ("def%d" % idx)
            key = f"{stem}__{suffix}"
            entries.append(_entry(key, name, defi, etype))
    return entries


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    point_dir = argv[1]
    out = (
        argv[2]
        if len(argv) > 2
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), "grammar_ko.json")
    )
    entries = generate(point_dir)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=1)
    n_focus = sum(1 for e in entries if e["focus"])
    print(f"wrote {len(entries)} records -> {out} (with focus: {n_focus})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
