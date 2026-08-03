-- Add fields to support YouTube video books.
--
-- BkBookType: '' = regular book, 'youtube' = YouTube video book.
ALTER TABLE books ADD COLUMN BkBookType TEXT NOT NULL DEFAULT '';

-- BkSrtData: JSON array of subtitle cues, e.g.
--   [{"start": 0.0, "end": 5.2, "text": "..."}, ...]
-- Times are in seconds.  Only used for youtube books.
ALTER TABLE books ADD COLUMN BkSrtData TEXT;

-- BkVideoCurrentPos: last YouTube playback position in seconds.
ALTER TABLE books ADD COLUMN BkVideoCurrentPos FLOAT;
