-- Add Korean / Kiwi parser settings columns to the languages table.
-- These columns only have meaning when LgParserType = 'korean'.
-- Defaults match the legacy behaviour so existing behaviour is preserved.

-- Tokenization granularity:
--   'morpheme' = split into individual morphemes (e.g. 예상+하+었+는데)
--   'lemma'    = group adjacent morphemes that belong to the same lemma (e.g. 예상하다)
--   'eojeol'   = whole 어절 (Korean word block) as one token (old SpaceDelimited behaviour)
ALTER TABLE languages ADD COLUMN LgKiwiTokenizerMode TEXT NOT NULL DEFAULT 'morpheme';

-- Whether stemming / lemmatization is enabled in the popup / term form.
-- When enabled, the get_lemma output is used to resolve the dictionary form.
ALTER TABLE languages ADD COLUMN LgKiwiStemming INTEGER NOT NULL DEFAULT 1;

-- When enabled, grammatical particles (J*) and endings (E*) are not
-- exposed as independent clickable tokens (they remain present in the
-- raw text for sentence correctness but are not selectable word tokens).
ALTER TABLE languages ADD COLUMN LgKiwiFilterParticles INTEGER NOT NULL DEFAULT 0;

-- When enabled, adjacent noun morphemes that form a compound noun are
-- merged into a single clickable token (e.g. 독도+이슈 → 독도이슈).
ALTER TABLE languages ADD COLUMN LgKiwiJoinCompoundNouns INTEGER NOT NULL DEFAULT 0;
