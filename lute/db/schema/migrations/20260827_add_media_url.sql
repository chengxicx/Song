-- Add BkMediaURL to support "video" type books imported from an online
-- media URL that is too large to download locally (>= 20 MB).  When the
-- media is stored locally the book uses BkAudioFilename instead; this
-- column holds the remote URL to stream from directly.
ALTER TABLE books ADD COLUMN BkMediaURL TEXT;