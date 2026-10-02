-- Per-language grammar-panel groups the reader hides, pipe-delimited:
-- level tokens (JLPT N5..N1, TOPIK bands, CEFR A1..C2) and the Japanese
-- aggregate keys (basic_forms, basic_particles).
-- Optional; empty/NULL means nothing is hidden.
ALTER TABLE languages ADD COLUMN LgHiddenGrammarLevels TEXT;
