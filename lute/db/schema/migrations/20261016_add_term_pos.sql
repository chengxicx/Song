-- Part-of-speech captured at page-parse time, so a term can be
-- auto-tagged from the sentence it actually appeared in.
--
-- WoPos holds the parser's POS tuple for the page token, joined with
-- "|": 動詞|非自立可能|*|*|五段-サ行|意志推量形
--
-- It is *stored* rather than recomputed at save time because re-parsing
-- a term's text on its own gives a different answer than parsing the
-- sentence did.  Sudachi reads 出そう in 「声を出そうとしましたが」 as
-- 動詞/五段-サ行/意志推量形, but reads the bare string 出そう as
-- 出(動詞/下一段-ダ行/連用形-一般) + そう(形状詞).  The same mismatch is
-- why Term.romanization can disagree with the furigana on the page.
--
-- Terms created before this migration have WoPos NULL and simply never
-- get an automatic tag.
ALTER TABLE words ADD COLUMN WoPos VARCHAR(64);

-- tags.TgText declared VARCHAR(20), but a full POS tag is longer than
-- that: 動詞/非自立可能/文語四段-ラ行/連体形-一般 is 23 characters.
-- Widen it to 50.
--
-- SQLite does not enforce or truncate VARCHAR lengths, so this rebuild
-- changes no behaviour -- it is here so that the declared width stops
-- lying, and so that the schema is ready if the length ever becomes a
-- real constraint.  The three-step copy is the documented SQLite way to
-- change a column definition; TgID is copied verbatim so the
-- wordtags rows that point at it keep pointing at the same tag.
--
-- foreign_keys is turned off for the DROP: wordtags.WtTgID cascades on
-- delete, and the app turns foreign keys ON for its own connections
-- (lute/app_factory.py).  The migrator's connection has them off by
-- default, but this must not depend on that.
PRAGMA foreign_keys = OFF;
CREATE TABLE tags_new (
    TgID INTEGER NOT NULL,
    TgText VARCHAR(50) NOT NULL,
    TgComment VARCHAR(200) NOT NULL DEFAULT '',
    PRIMARY KEY (TgID)
);
INSERT INTO tags_new (TgID, TgText, TgComment)
    SELECT TgID, TgText, TgComment FROM tags;
DROP TABLE tags;
ALTER TABLE tags_new RENAME TO tags;
CREATE UNIQUE INDEX TgText ON tags (TgText);
PRAGMA foreign_keys = ON;
