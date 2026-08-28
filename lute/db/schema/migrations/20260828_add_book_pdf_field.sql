-- Add field to support PDF book imports.
--
-- BkPdfPath: relative path under the static folder where the
--   imported PDF file lives, e.g. "pdf/<uuid>/file.pdf".
--   The reading screen renders the original PDF via pdf.js and
--   overlays clickable word hotspots extracted at render time.
ALTER TABLE books ADD COLUMN BkPdfPath TEXT;
