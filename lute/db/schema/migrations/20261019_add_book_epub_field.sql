-- Add field to support EPUB reader-mode imports.
--
-- BkEpubPath: relative path under the static folder of the chapter's
--   reader-ready HTML file, e.g. "epub/<uuid>/ch/003.html".
--   Reader-mode EPUB books keep the chapter's original markup (inline
--   styles, images, tables) so the reading screen can inject word spans
--   into it instead of rendering the flattened plain text.
ALTER TABLE books ADD COLUMN BkEpubPath TEXT;
