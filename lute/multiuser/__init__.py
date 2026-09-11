"""
Multi-user mode.

One SQLite database and media directory per user, routed per request:

- context:  request-scoped identity (ContextVar)
- paths:    per-user filesystem layout
- store:    users master db (<datapath>/users.db) + mode flag
- switching: enable/disable mode with data migration
- routes:   /login /logout and /users management

Single-user mode (the default state, no users.db) is unaffected:
the auth gate is a no-op and all paths resolve to the base config.
"""
