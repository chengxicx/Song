-- The AI sentence translation tab becomes a first-class dictionary row.
--
-- The reader's Sentence tab used to prepend a hardcoded AI tab in
-- lute/read/routes.py, so the AI tab could not be seen or ordered in
-- the Language form.  It is now a 'languagedicts' row of type 'AI':
-- the row's position is the tab's position, like every other tab.
--
-- Back-fill: every language whose "AI sentence translation" switch
-- (LgAiSentenceTranslation, added in 20261104) is on gets one AI row
-- at position 0 -- the position the hardcoded tab had, so existing
-- readers see no change.  Languages with the switch off get no row:
-- the switch stays the master gate, and turning it on later creates
-- the row when the language is next saved (see
-- _sync_ai_dictionary_row in lute/language/routes.py).
--
-- LdDictURI stores the /ai/sentence URL so the row satisfies the
-- model's NOT NULL column; the reader builds the URL from the page's
-- language id at render time (see Language.active_dict_uris), so a
-- row copied between languages still points at the right frame.

UPDATE languagedicts
   SET LdSortOrder = LdSortOrder + 1
 WHERE LdLgID IN (
    SELECT LgID FROM languages
     WHERE COALESCE(LgAiSentenceTranslation, 1) = 1
       AND NOT EXISTS (
          SELECT 1 FROM languagedicts ld
           WHERE ld.LdLgID = languages.LgID AND ld.LdType = 'AI'
       )
);

INSERT INTO languagedicts (LdLgID, LdUseFor, LdType, LdDictURI, LdIsActive, LdIsHidden, LdSortOrder)
SELECT LgID,
       'sentences',
       'AI',
       '/ai/sentence?lang=' || LgID || '&q=[LUTE]',
       1,
       0,
       0
  FROM languages
 WHERE COALESCE(LgAiSentenceTranslation, 1) = 1
   AND NOT EXISTS (
      SELECT 1 FROM languagedicts ld
       WHERE ld.LdLgID = languages.LgID AND ld.LdType = 'AI'
   );

-- At most one AI row per language.  SQLite supports partial indexes.
CREATE UNIQUE INDEX IF NOT EXISTS ux_languagedicts_ai
    ON languagedicts (LdLgID)
 WHERE LdType = 'AI';
