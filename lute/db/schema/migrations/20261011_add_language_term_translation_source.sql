-- Where a new term's translation comes from.
--
-- The term form auto-fills the translation for words the reader has no
-- answer for yet.  That used to always be online machine translation
-- (Google / MyMemory / backend fallback, see tts-translate.js).  With a
-- per-language local (MDX) dictionary installed, the dictionary's own
-- definition is often the better answer, so the source is now a choice:
-- 'online' (the old behaviour) or 'local_dict' -- which tries the
-- local dictionary first and falls back to online when it has no entry.

ALTER TABLE languages ADD COLUMN LgTermTranslationSource TEXT NOT NULL DEFAULT 'online';
