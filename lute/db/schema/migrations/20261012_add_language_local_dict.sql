-- Per-language local (MDX) dictionary selection.
--
-- The server keeps a *pool* of converted dictionaries in
-- <static>/dict/ (see lute/dict/service.py): the phone's default file
-- plus anything else the admin imported.  A language now picks which
-- one of them it reads -- a Japanese language reads the Japanese
-- dictionary, Korean the Korean one.  Empty/NULL means "none".

-- File name of the dictionary in the pool this language uses.
ALTER TABLE languages ADD COLUMN LgLocalDict TEXT;

-- Whether the term popup adds this language's dictionary definition to
-- the popup, for terms with no translation yet.
--
-- This used to be the global user setting `term_popup_show_dict_gloss`,
-- which could only ever be right for one of the languages a user reads
-- (the dictionary itself was a single hard-coded file, shown for every
-- language).  Default 1 keeps the old behaviour, which had it on.
ALTER TABLE languages ADD COLUMN LgTermPopupShowDictGloss BOOLEAN NOT NULL DEFAULT 1;

-- Back-fill the one language the old behaviour was right for.
--
-- Before this migration every language showed the same hard-coded
-- Japanese dictionary; that was only correct for Japanese, and wrong
-- (and confusing) for everything else.  Keep the tab where it worked,
-- and leave the rest at "none" so they don't silently inherit a
-- dictionary in the wrong language.
UPDATE languages
   SET LgLocalDict = 'ja_zh_shogakukan_v2.db'
 WHERE LgLocalDict IS NULL
   AND (LOWER(LgName) LIKE '%japan%'
        OR LgName LIKE '%日本語%'
        OR LgName LIKE '%日语%'
        OR LgName LIKE '%日語%');

-- The old global switch is gone; its value moved to the column above
-- (where it is already 1, matching the old default).
DELETE FROM settings WHERE StKey = 'term_popup_show_dict_gloss';
