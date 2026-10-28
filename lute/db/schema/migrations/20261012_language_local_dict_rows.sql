-- Local (MDX) dictionaries become first-class dictionary rows.
--
-- The language used to pick ONE pool file (languages.LgLocalDict), with
-- a separate first/last position setting (LgLocalDictPosition).  Now a
-- language lists dictionaries of a new type 'localdict' in the regular
-- languagedicts table: several local dictionaries per language, ordered
-- by drag-and-drop like every other tab, hidden like every other tab.
--
-- Back-fill: one local row per language that had a choice.
--   position 'first' (or unset)  -> existing rows shift down by one,
--                                   the local row takes sort_order 0
--   position 'last'              -> the local row goes after everything
--
-- The LgLocalDict / LgLocalDictPosition columns stay (SQLite drop is
-- more risk than an unread column) but nothing reads them any more.

UPDATE languagedicts
   SET LdSortOrder = LdSortOrder + 1
 WHERE LdLgID IN (
    SELECT LgID FROM languages
     WHERE LgLocalDict IS NOT NULL AND LgLocalDict != ''
       AND (LgLocalDictPosition IS NULL OR LgLocalDictPosition != 'last')
);

INSERT INTO languagedicts (LdLgID, LdUseFor, LdType, LdDictURI, LdIsActive, LdIsHidden, LdSortOrder)
SELECT LgID,
       'terms',
       'localdict',
       LgLocalDict,
       1,
       0,
       CASE WHEN LgLocalDictPosition = 'last'
            THEN (SELECT COALESCE(MAX(ld2.LdSortOrder), 0) + 1
                    FROM languagedicts ld2 WHERE ld2.LdLgID = languages.LgID)
            ELSE 0
       END
  FROM languages
 WHERE LgLocalDict IS NOT NULL AND LgLocalDict != '';
