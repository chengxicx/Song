"""
Extract i18n keys from the codebase.

Scans lute/templates (Jinja translation calls), lute/static/js and
templates' inline scripts (I18N.t(...)), and lute/*.py (t(...))
for translation calls, and reports which keys are missing from the
zh/ja/ko dictionaries in lute/i18n/.

Usage:
    python scripts/extract_i18n_keys.py             # report missing keys
    python scripts/extract_i18n_keys.py --write     # write skeleton keys
                                                    # (value = "") to the
                                                    # json files
"""

import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "lute")
I18N_DIR = os.path.join(ROOT, "i18n")

# t('...') / t("..."), not preceded by a dot or word char (so
# .split(...), datetime etc. don't match).  The quote style is captured
# and matched by backreference, so double-quoted keys may contain
# apostrophes (t("Lute's manual")) and vice versa.
CALL_RE = re.compile(r"""(?<![.\w])t\(\s*(['"])(.*?)\1\s*\)""")

# Third-party code never carries translatable strings.
EXCLUDED_DIRS = ("vendor", "node_modules", "__pycache__")


def find_keys():
    "Return the sorted set of all keys used in t()/I18N.t() calls."
    keys = set()
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        if any(part in EXCLUDED_DIRS for part in dirpath.split(os.sep)):
            continue
        for fn in filenames:
            if not fn.endswith((".html", ".py", ".js")):
                continue
            if fn.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in CALL_RE.finditer(text):
                keys.add(m.group(2))
    return keys


def load_dict(lang):
    "Load the given language's dictionary."
    path = os.path.join(I18N_DIR, f"{lang}.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_dict(lang, d):
    "Save the given language's dictionary, keys sorted."
    path = os.path.join(I18N_DIR, f"{lang}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main():
    write = "--write" in sys.argv
    keys = find_keys()
    missing_by_lang = {}
    for lang in ("zh", "ja", "ko"):
        d = load_dict(lang)
        missing = sorted(k for k in keys if k not in d)
        missing_by_lang[lang] = missing
        stale = sorted(k for k in d if k not in keys)
        if write:
            for k in missing:
                d[k] = ""
            save_dict(lang, d)
        print(
            f"{lang}: {len(keys)} keys used, {len(missing)} missing, {len(stale)} unused in dict"
        )
        if stale:
            print(
                f"  {lang} unused keys: {stale[:20]}{'...' if len(stale) > 20 else ''}"
            )
    if not write:
        print(f"\ntotal distinct keys: {len(keys)}")


if __name__ == "__main__":
    main()
