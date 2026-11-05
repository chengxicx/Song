-- Per-language AI provider for sentence translation.
--
-- The AI dictionary row can now pin which configured provider its
-- sentence tab uses.  '' means Default: follow the provider enabled in
-- Settings, so every existing row keeps its old behaviour.
ALTER TABLE languagedicts ADD COLUMN LdAiProvider VARCHAR(20) NOT NULL DEFAULT '';
