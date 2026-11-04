"""
Web-session AI providers for sentence translation.

The reader's "Sentence" tab used to embed https://chat.qwen.ai/?q=[LUTE]
in an iframe, which Qwen refuses to frame (frame-ancestors 'self') and
whose login cannot survive a cross-site iframe (CHIPS partitioning /
SameSite=Lax cookies).  This package instead calls the provider's web
endpoint from the Lute server, using a session token the user copied
out of their own browser after logging in on the provider's site.

Credentials are per user (each multi-user account has its own database,
see lute/multiuser/paths.py) and encrypted at rest (lute/ai/crypto.py).
"""
