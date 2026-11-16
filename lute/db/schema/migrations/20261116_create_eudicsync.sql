-- Per-term sync state for the Eudic (欧路词典) studylist.
--
-- Lute's words table has no reliable watermark for incremental sync:
-- WoCreated is reset by a trigger when a term leaves Unknown status, and
-- WoStatusChanged only moves when the status changes, so neither can say
-- "what changed since the last run".  Sync is therefore a three-way
-- reconciliation against the stored baseline below, not a time delta.
--
-- One row per (word, eudic bucket, eudic category):
--   EsBucket        en | fr | de | es.  These are Eudic *product* buckets,
--                   not the language of the words -- a Japanese list lives
--                   in the "en" bucket as its own category.
--   EsCategoryID    the studylist (生词本) inside that bucket.
--   EsRemoteWord    the remote string verbatim, e.g. "俺 [おれ]".  Eudic's
--                   storage key includes the bracketed reading, so pushing
--                   a rebuilt form would create a second entry on every
--                   run; the original string is reused instead.
--   EsWordKey       normalised match key (ZWS dropped, reading stripped,
--                   lowercased) used to align with Lute terms.
--   EsRemoteStar    star seen in the last snapshot; NULL = not on Eudic.
--   EsLastSyncedStar / EsLastSyncedStatus
--                   the values both sides agreed on last time, -1 = never.
--                   Comparing these against the current values is what
--                   separates "local changed" from "remote changed".
--   EsRemoteAddTime Eudic only exposes a creation time, never a modified
--                   time; kept for display, never used as a watermark.
--   EsRemotePresent 1 if the word was still on Eudic in the last snapshot.
--   EsLastError     per-word failure (a batch can fail without rolling
--                   back, so errors are recorded per word and retried).
--   EsFailCount     consecutive failures; reset on success.
--
-- Written by lute/eudic/service.py, read by lute/eudic/routes.py.
CREATE TABLE IF NOT EXISTS "eudicsync" (
       "EsID" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
       "EsWoID" INTEGER NOT NULL,
       "EsLgID" INTEGER NOT NULL,
       "EsBucket" VARCHAR(2) NOT NULL,
       "EsCategoryID" VARCHAR(40) NOT NULL DEFAULT '0',
       "EsRemoteWord" VARCHAR(250) NOT NULL,
       "EsWordKey" VARCHAR(250) NOT NULL,
       "EsRemoteStar" INTEGER,
       "EsLastSyncedStar" INTEGER NOT NULL DEFAULT -1,
       "EsLastSyncedStatus" INTEGER NOT NULL DEFAULT -1,
       "EsRemoteAddTime" VARCHAR(30),
       "EsRemotePresent" INTEGER NOT NULL DEFAULT 0,
       "EsLastPushedAt" DATETIME,
       "EsLastPulledAt" DATETIME,
       "EsLastError" VARCHAR(500),
       "EsLastErrorAt" DATETIME,
       "EsFailCount" INTEGER NOT NULL DEFAULT 0,
       "EsCreated" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
       "EsUpdated" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
       FOREIGN KEY("EsWoID") REFERENCES "words" ("WoID") ON DELETE CASCADE,
       FOREIGN KEY("EsLgID") REFERENCES "languages" ("LgID") ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS "ix_eudicsync_identity"
  ON "eudicsync" ("EsWoID", "EsBucket", "EsCategoryID");
CREATE INDEX IF NOT EXISTS "ix_eudicsync_scope"
  ON "eudicsync" ("EsLgID", "EsBucket", "EsCategoryID");
CREATE INDEX IF NOT EXISTS "ix_eudicsync_error"
  ON "eudicsync" ("EsLgID", "EsLastErrorAt");

-- Eudic connection settings.
--
-- Deliberately NOT in the generic settings table: lute/app_factory.py
-- dumps every row of that table into the LUTE_USER_SETTINGS javascript
-- namespace on every page, so a credential stored there would be
-- embedded in every page's HTML.  One row per user database.
--
--   EcApiKey  ciphertext from lute.ai.crypto.encrypt(), never plaintext
--   EcScopes  JSON: {"<Lute LgID>": {"bucket": "en",
--                                   "category_id": "...",
--                                   "category_name": "日文生词"}}
CREATE TABLE IF NOT EXISTS "eudicconfig" (
       "EcID" INTEGER NOT NULL PRIMARY KEY,
       "EcApiKey" TEXT NOT NULL DEFAULT '',
       "EcScopes" TEXT NOT NULL DEFAULT '{}',
       "EcUpdated" DATETIME
);
