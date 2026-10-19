"""Authored example glosses for the merged grammar corpora.

The Spanish / French / German / Russian corpora carry a Chinese gloss for
every example (``example_zh``, taken from the merge materials) and, since
the English back-fill, an English one.  The English glosses are **authored**
material -- ``scripts/grammar_materials_<lang>/example_en*.json`` -- rather
than text typed into the corpus, because each merge rewrites the corpus from
its materials and would otherwise drop them.  Reading them here keeps the
"library is a pure function of the materials" invariant true.

An entry is either ``[[source sentence, translation], ...]`` -- the source
carried so a pairing mistake is detectable -- or ``[translation, ...]``,
positional, used for the Russian corpus whose text carries transcription
damage no one wants to retype.  Either shape must have one gloss per example
or the whole row is dropped: a translation under the wrong sentence teaches
the wrong thing.
"""

import glob
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(lang, field="en"):
    """
    ``{row key: [translation, ...]}`` for one corpus.

    Reads ``example_<field>.json`` and every ``example_<field>_<n>.json``
    beside it (a several-hundred-row corpus is easier to author in parts),
    and normalises both entry shapes to a plain list of translations.
    """
    folder = os.path.join(_HERE, "grammar_materials_" + lang)
    glosses = {}
    for part in sorted(glob.glob(os.path.join(folder, "example_" + field + "*.json"))):
        with open(part, encoding="utf-8") as fh:
            for key, value in json.load(fh).items():
                glosses[key] = [
                    (pair[1] if isinstance(pair, (list, tuple)) else pair).strip()
                    for pair in value
                ]
    return glosses


def attach(row, glosses, field="en"):
    """
    Add ``example_<field>`` to a corpus row, when the counts line up.

    Mutates and returns ``row``; a row with no authored glosses, or whose
    gloss count does not match its examples, comes back unchanged.
    """
    values = glosses.get(row.get("key") or "")
    if not values or len(values) != len(row.get("examples") or []):
        return row
    row["example_" + field] = values
    return row
