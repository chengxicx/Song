-- Add fields to support Mokuro manga book imports.
--
-- BkMangaPath: relative directory under the static folder where the
--   extracted manga files live, e.g. "manga/<uuid>".
-- BkMangaData: the full .mokuro JSON (pages, blocks, image paths), so
--   the reading screen can render images + overlaid text blocks.
ALTER TABLE books ADD COLUMN BkMangaPath TEXT;
ALTER TABLE books ADD COLUMN BkMangaData TEXT;

-- Store pre-computed word count for manga books (computed from mokuro
-- JSON text lines).  Regular books use SUM(TxWordCount) instead.
ALTER TABLE bookstats ADD COLUMN manga_word_count INTEGER;