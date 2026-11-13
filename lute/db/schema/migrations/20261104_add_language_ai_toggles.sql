-- Per-language switches for the two AI features (sentence translation
-- in the reader's Sentence tab, and the Grammar panel's AI analysis).
-- Both default to 1 so every existing language keeps the behaviour it
-- had before these columns existed; the reader opts out per language
-- in the Language form.

ALTER TABLE languages ADD COLUMN LgAiSentenceTranslation INTEGER NOT NULL DEFAULT 1;

ALTER TABLE languages ADD COLUMN LgAiGrammarAnalysis INTEGER NOT NULL DEFAULT 1;
