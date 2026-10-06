-- Shadowing (read-aloud) attempts: one row per scored take.
-- Written by the background scoring tasks (lute/read/shadowing.py),
-- from the reading page and from shadowing review cards alike; feeds
-- the stats page and gives the practice a history.

CREATE TABLE IF NOT EXISTS "shadowattempts" (
       "SaID" INTEGER NOT NULL,
       "SaLgID" INTEGER NOT NULL,
       "SaBkID" INTEGER,
       "SaSource" VARCHAR(20) NOT NULL DEFAULT 'read',
       "SaSentence" TEXT,
       "SaScore" INTEGER NOT NULL DEFAULT '0',
       "SaDuration" FLOAT,
       "SaTokensPerMin" FLOAT,
       "SaEngine" VARCHAR(20),
       "SaTokens" TEXT,
       "SaCreated" DATETIME,
       PRIMARY KEY ("SaID"),
       FOREIGN KEY("SaLgID") REFERENCES "languages" ("LgID") ON UPDATE NO ACTION ON DELETE CASCADE,
       FOREIGN KEY("SaBkID") REFERENCES "books" ("BkID") ON UPDATE NO ACTION ON DELETE CASCADE
);

CREATE INDEX "ix_shadowattempts_created" ON "shadowattempts" ("SaCreated");
