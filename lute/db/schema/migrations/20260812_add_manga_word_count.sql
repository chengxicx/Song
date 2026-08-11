-- Add the pre-computed word-count column to bookstats.

-- This migration exists as a separate file (instead of being folded into
-- 20260811_add_manga_fields.sql) because 20260811 may already be recorded
-- in an existing database's _migrations table from an earlier version of
-- the file that did not include this column.  Migrations are tracked by
-- filename, so editing 20260811 after the fact would silently skip this
-- column on such databases.
ALTER TABLE bookstats ADD COLUMN manga_word_count INTEGER;