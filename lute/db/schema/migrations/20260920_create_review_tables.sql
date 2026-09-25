-- Review queue: admission specs, scheduled cards, review logs.

CREATE TABLE IF NOT EXISTS "reviewspecs" (
       "RsID" INTEGER NOT NULL,
       "RsName" VARCHAR(200) NOT NULL UNIQUE,
       "RsCriteria" VARCHAR(1000) NOT NULL DEFAULT '',
       "RsCardTypes" VARCHAR(200) NOT NULL DEFAULT '{"recognition": 1, "recall": 0, "cloze": 1}',
       "RsActive" TINYINT NOT NULL DEFAULT '1',
       PRIMARY KEY ("RsID")
);

CREATE TABLE IF NOT EXISTS "reviewcards" (
       "RcID" INTEGER NOT NULL,
       "RcWoID" INTEGER NOT NULL,
       "RcCardType" VARCHAR(20) NOT NULL,
       "RcDue" DATETIME,
       "RcState" INTEGER NOT NULL DEFAULT '0',
       "RcStability" FLOAT,
       "RcDifficulty" FLOAT,
       "RcReps" INTEGER NOT NULL DEFAULT '0',
       "RcLapses" INTEGER NOT NULL DEFAULT '0',
       "RcLastReview" DATETIME,
       "RcCreated" DATETIME,
       "RcSpecID" INTEGER,
       PRIMARY KEY ("RcID"),
       UNIQUE ("RcWoID", "RcCardType"),
       FOREIGN KEY("RcWoID") REFERENCES words ("WoID"),
       FOREIGN KEY("RcSpecID") REFERENCES reviewspecs ("RsID")
);

CREATE TABLE IF NOT EXISTS "reviewlogs" (
       "RlID" INTEGER NOT NULL,
       "RlRcID" INTEGER NOT NULL,
       "RlReviewTime" DATETIME NOT NULL,
       "RlRating" INTEGER NOT NULL,
       "RlRepsBefore" INTEGER NOT NULL DEFAULT '0',
       "RlData" TEXT,
       PRIMARY KEY ("RlID"),
       FOREIGN KEY("RlRcID") REFERENCES reviewcards ("RcID")
);

CREATE INDEX "ix_reviewcards_due" ON "reviewcards" ("RcDue");
CREATE INDEX "ix_reviewlogs_card" ON "reviewlogs" ("RlRcID");
