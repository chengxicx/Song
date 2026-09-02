# `lute3-korean`

A Korean parser for Lute (`lute3`) using the
[kiwipiepy](https://github.com/bab2min/kiwipiepy) library, a fast
Korean morphological analyzer based on the Kiwi C++ library.

## Installation

```
pip install lute3-korean
```

See the [Lute manual](https://luteorg.github.io/lute-manual/install/plugins.html)
for how Lute discovers parser plugins.

## Usage

When this parser is installed, you can add "Korean" as a language to
Lute and select the **Lute Korean** parser.

## Segmentation

By default, text is split into individual **morphemes**
(`예상했었는데` → 예상 + 하 + 었 + 는데), which is recommended for
learners.  Spaces between 어절 (whitespace-delimited blocks) are
preserved.

The parser reads optional `kiwi_*` attributes from the language
object if they exist, and otherwise uses the defaults below:

| Setting | Default | Meaning |
|---|---|---|
| `kiwi_tokenizer_mode` | `morpheme` | `morpheme` (fine-grained), `lemma` (predicate merged to dictionary form, e.g. 예상했었는데 → 예상하다), or `eojeol` (whole block as one token) |
| `kiwi_filter_particles` | `False` | Mark particles (조사 J*) and endings (어미 E*) as non-clickable tokens |
| `kiwi_join_compound_nouns` | `False` | Merge consecutive noun morphemes into one token |
| `kiwi_stemming` | `True` | `get_lemma` resolves inflected forms to their dictionary form (먹었어 → 먹다) |

## Lemmas

Inflected forms resolve to their base dictionary form for the popup /
parent term lookup (먹었어 → 먹다, 예상했었는데 → 예상하다), including
light-verb compounds (독립+하다, 행복+하다).
