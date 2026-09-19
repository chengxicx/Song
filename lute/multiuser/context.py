"""
Request-scoped identity for multi-user mode.

The current username lives in a ContextVar.  It is set:

- by the auth gate before_request hook (request handling), or
- explicitly via user_scope() (boot-time seeding, CLI, tests).

When no user is set -- single-user mode, or pre-login requests --
scope key falls back to DEFAULT_SCOPE, which maps to the base
database and data paths, preserving single-user behavior exactly.
"""

import contextvars
from contextlib import contextmanager

DEFAULT_SCOPE = "__default__"

_current_user = contextvars.ContextVar("lute_current_user", default=None)


def set_current_user(username):
    "Set (or clear with None) the current user for this context."
    _current_user.set(username)


def get_current_user():
    "The current username, or None if unset (single-user scope)."
    return _current_user.get()


def current_scope_key():
    "Key used to partition per-user caches (settings buckets etc.)."
    return _current_user.get() or DEFAULT_SCOPE


@contextmanager
def user_scope(username):
    "Run a block as the given user (boot-time seeding, tests)."
    token = _current_user.set(username)
    try:
        yield
    finally:
        _current_user.reset(token)
