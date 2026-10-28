-- Where the reading page puts the Local dictionary tab.
--
-- The Local tab used to be hard-coded first in the dictionary strip
-- (see _term_dicts_for in lute/read/routes.py), because it is the only
-- tab that works offline.  But a reader who mostly uses the online
-- dictionaries has to click past it every time, so the position is now
-- a per-language choice.  'first' keeps the old behaviour.

ALTER TABLE languages ADD COLUMN LgLocalDictPosition TEXT NOT NULL DEFAULT 'first';
