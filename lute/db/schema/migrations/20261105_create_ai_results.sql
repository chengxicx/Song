-- Persistent AI results, replacing the settings-based TTL cache.
-- Key = sentence + target language + provider entry id + model + type.
-- Different models / target languages are separate rows (different keys)
-- so switching models never overwrites the other result.

CREATE TABLE IF NOT EXISTS "airesults" (
       "id"           INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
       "key"          VARCHAR(255) NOT NULL,
       "result_type"  VARCHAR(20) NOT NULL DEFAULT 'sentence',
       "book_id"      INTEGER,
       "lang_id"      INTEGER,
       "sentence"     TEXT NOT NULL DEFAULT '',
       "result_json"  TEXT NOT NULL DEFAULT '',
       "provider_id"  VARCHAR(64) NOT NULL DEFAULT '',
       "model"        VARCHAR(255) NOT NULL DEFAULT '',
       "target_lang"  VARCHAR(64) NOT NULL DEFAULT '',
       "source"       VARCHAR(20) NOT NULL DEFAULT 'live',
       "token_usage"  TEXT,
       "hit_count"    INTEGER NOT NULL DEFAULT 0,
       "created_at"   DATETIME NOT NULL,
       "updated_at"   DATETIME NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS "ix_airesults_key" ON "airesults" ("key");
CREATE INDEX IF NOT EXISTS "ix_airesults_book_id" ON "airesults" ("book_id");
CREATE INDEX IF NOT EXISTS "ix_airesults_lang_id" ON "airesults" ("lang_id");
