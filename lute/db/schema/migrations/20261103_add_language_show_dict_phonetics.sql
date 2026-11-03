-- Per-language toggle for pronunciation notation in the Local
-- dictionary card (and an English term's IPA reading in the popup).
-- Optional; NULL/0 means off, which is the default: the card shows the
-- headword, POS, definitions and examples, but no BrE/NAmE /.../ run.
-- The lookup and the stored glossary are untouched -- see
-- gloss_format.display_blocks.

ALTER TABLE languages ADD COLUMN LgShowDictPhonetics INTEGER DEFAULT 0;
