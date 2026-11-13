"""
Official OpenAI / Anthropic API support for sentence translation and
grammar analysis.

The reader's Sentence and Grammar tabs call the API from the Lute server
with a key the user pasted into Settings.  No browser session, cookie,
iframe or proof-of-work replay is involved.

Credentials are per user (each multi-user account has its own database,
see lute/multiuser/paths.py) and encrypted at rest (lute/ai/crypto.py).
"""
