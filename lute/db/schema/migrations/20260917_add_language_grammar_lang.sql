-- Per-language display language for grammar analysis results.
-- Optional; empty/"en" keeps English, "zh" renders Chinese descriptions.
ALTER TABLE languages ADD COLUMN LgGrammarTranslateLang TEXT;