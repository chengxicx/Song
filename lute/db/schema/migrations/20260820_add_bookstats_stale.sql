-- Add a "stale" flag to bookstats.
--
-- Previously mark_stale() deleted the bookstats row, which made the home
-- page re-calculate stats synchronously on every return (expensive for
-- long books, and the Status / New word columns went blank meanwhile).
-- Now mark_stale() keeps the last-known values and only sets this flag,
-- so the home page can keep showing old stats while a recompute happens.
ALTER TABLE bookstats ADD COLUMN BkStatsStale BOOLEAN NOT NULL DEFAULT 0;
