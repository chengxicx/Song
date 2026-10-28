-- Hide an online dictionary without deleting it.
--
-- A language can carry many dictionaries; some are rarely used but the
-- reader does not want to re-enter them, so they stay configured and
-- active while their tab disappears from the reading page's dictionary
-- strip (see Language.active_dict_uris, the single place every term and
-- sentence tab list is built from).  Default 0 = shown, the old
-- behaviour.

ALTER TABLE languagedicts ADD COLUMN LdIsHidden BOOLEAN NOT NULL DEFAULT 0;
