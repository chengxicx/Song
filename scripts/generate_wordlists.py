"""One-time generator: transform GitHub word lists into per-level JSON.

Inputs (downloaded to /tmp/lute_data):
  - ENGLISH_CERF_WORDS.csv  (katherine-welbourne/english-cefr-text-generator)  headword,CEFR
  - results.tsv              (julienshim/combined_korean_vocabulary_list)       rank,word,pos,hanja,explanation,nikl_level,topik_level

Outputs (written into the repo, committed):
  - lute/stats/cefr_data/vocab-{a1,a2,b1,b2,c1,c2}.json
  - lute/stats/topik_data/vocab-{a,b,c}.json
"""
import csv
import json
import os
import re
from collections import defaultdict

REPO = "/Users/cxi/Documents/lutedev/lute-v3/lute/stats"
DATA = "/tmp/lute_data"

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
TOPIK_LEVELS = ["A", "B", "C"]


def _write(name, level, entries):
    path = os.path.join(REPO, name, f"vocab-{level.lower()}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, ensure_ascii=False, indent=2)
    print(f"  {os.path.basename(path):<24} {len(entries):>5}")


def build_cefr():
    word_level = {}
    skipped = 0
    with open(os.path.join(DATA, "ENGLISH_CERF_WORDS.csv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            head = (row.get("headword") or "").strip()
            lvl = (row.get("CEFR") or "").strip().upper()
            if not head or lvl not in CEFR_LEVELS:
                skipped += 1
                continue
            cur = word_level.get(head)
            if cur is None or CEFR_LEVELS.index(lvl) < CEFR_LEVELS.index(cur):
                word_level[head] = lvl
    print(f"CEFR: {len(word_level)} unique headwords, skipped {skipped} rows")
    per = defaultdict(list)
    for word, level in word_level.items():
        per[level].append({"word": word, "reading": "", "meanings": []})
    for level in CEFR_LEVELS:
        per[level].sort(key=lambda e: e["word"])
        _write("cefr_data", level, per[level])


def build_topik():
    word_level = {}
    total = 0
    with open(os.path.join(DATA, "results.tsv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            raw_word = (row.get("word") or "").strip()
            lvl = (row.get("topik_level") or "").strip().upper()
            if not raw_word or lvl not in TOPIK_LEVELS:
                continue
            total += 1
            word = re.sub(r"[0-9]+$", "", raw_word).strip()
            entry = {
                "word": raw_word,
                "reading": (row.get("hanja") or "").strip(),
                "meanings": [e for e in [(row.get("explanation") or "").strip()] if e],
            }
            cur = word_level.get(word)
            if cur is None or TOPIK_LEVELS.index(lvl) < TOPIK_LEVELS.index(cur[0]):
                word_level[word] = (lvl, entry)
    print(f"TOPIK: {total} non-empty rows -> {len(word_level)} unique words")
    per = defaultdict(list)
    for word, (level, entry) in word_level.items():
        per[level].append(entry)
    for level in TOPIK_LEVELS:
        per[level].sort(key=lambda e: e["word"])
        _write("topik_data", level, per[level])


if __name__ == "__main__":
    build_cefr()
    build_topik()
    print("Done.")