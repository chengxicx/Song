-- Add fields to support Mokuro manga book imports.
--
-- BkMangaPath: relative directory under the static folder where the
--   extracted manga files live, e.g. "manga/<uuid>".
-- BkMangaData: the full .mokuro JSON (pages, blocks, image paths), so
--   the reading screen can render images + overlaid text blocks.
ALTER TABLE books ADD COLUMN BkMangaPath TEXT;
ALTER TABLE books ADD COLUMN BkMangaData TEXT;