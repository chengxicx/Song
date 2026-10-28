-- Whole-book grammar analyses, precomputed in the background.
--
-- The reader's "This book" scope used to re-run every grammar engine over
-- the whole text on each request: 1900 sentences is ~27s, which overruns
-- the proxy timeout and leaves the panel stuck.  The import path now
-- queues the book and a background worker stores the engine's output
-- here, so the read path is a single row lookup.
--
-- One row per book:
--   BgStatus    pending | running | ready | error | skipped
--   BgTextHash  sha1 of the analysed text; a mismatch means the book was
--               edited, and the row is recomputed.  NULL = never analysed.
--   BgDisplay   grammar_translate_lang used to build the descriptions
--               ("en", "zh", ...); a different setting rebuilds the row.
--   BgRules     fingerprint of the grammar rules/engine sources, so a
--               deploy that changes the library invalidates every row.
--   BgResults   JSON array, the engine's raw output BEFORE the language's
--               hidden_grammar_levels filter is applied.  Hiding a level is
--               a display choice and must not cost a recompute.
--   BgError     last failure message, truncated.
--   BgUpdated   when the row last changed state.
--
-- Written by lute/read/grammar_precompute.py; read by
-- lute/read/routes.py.  Deleting a book drops its row via the cascade.
CREATE TABLE IF NOT EXISTS "bookgrammar" (
       "BgBkID"     INTEGER NOT NULL,
       "BgStatus"   TEXT NOT NULL DEFAULT 'pending',
       "BgTextHash" TEXT,
       "BgDisplay"  TEXT,
       "BgRules"    TEXT,
       "BgResults"  TEXT,
       "BgError"    TEXT,
       "BgUpdated"  DATETIME,
       PRIMARY KEY ("BgBkID"),
       FOREIGN KEY("BgBkID") REFERENCES "books" ("BkID") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "ix_bookgrammar_status" ON "bookgrammar" ("BgStatus");
