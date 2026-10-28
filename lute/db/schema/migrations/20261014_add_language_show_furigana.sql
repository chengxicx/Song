-- Per-language furigana display toggle: when on (and the language is
-- Japanese), the reading page draws kana readings above kanji words.
-- Optional; NULL/0 means off.
ALTER TABLE languages ADD COLUMN LgShowFurigana INTEGER DEFAULT 0;
