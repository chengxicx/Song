"""
The --user gate for CLI commands that touch the database.

Multi-user mode partitions databases per user; a CLI command that writes
must refuse to guess which account to write as, or it would either raise
"no user scope is set" (loud, but late) or silently hit the wrong db.
"""

import click
import pytest

from lute.cli.commands import _user_scope_context
from lute.multiuser import context as mu_context
from lute.multiuser import store as mu_store


def _set_mode(monkeypatch, enabled, usernames=None):
    "Force what mu_store would answer, without touching users.db."
    monkeypatch.setattr(mu_store, "enabled", lambda: enabled)
    monkeypatch.setattr(
        mu_store, "users", lambda: [{"username": u} for u in (usernames or [])]
    )
    monkeypatch.setattr(
        mu_store,
        "get_user",
        lambda u: {"username": u} if u in (usernames or []) else None,
    )


def test_single_user_ignores_user_option(monkeypatch, capsys):
    "Naming a user in single-user mode is harmless, just noted."
    _set_mode(monkeypatch, enabled=False)
    with _user_scope_context("someone"):
        pass
    assert "--user is ignored" in capsys.readouterr().out


def test_single_user_without_user_option_is_clean(monkeypatch, capsys):
    _set_mode(monkeypatch, enabled=False)
    with _user_scope_context(None):
        pass
    assert capsys.readouterr().out == ""


def test_multiuser_requires_explicit_user(monkeypatch):
    "Refuse to guess: the error must name the existing accounts."
    _set_mode(monkeypatch, enabled=True, usernames=["<user>"])
    with pytest.raises(click.UsageError) as exc:
        _user_scope_context(None)
    assert "--user" in str(exc.value)
    assert "<user>" in str(exc.value)


def test_multiuser_rejects_unknown_user(monkeypatch):
    _set_mode(monkeypatch, enabled=True, usernames=["<user>"])
    with pytest.raises(click.UsageError) as exc:
        _user_scope_context("nobody")
    assert "Unknown user" in str(exc.value)


def test_multiuser_valid_user_sets_and_clears_scope(monkeypatch):
    _set_mode(monkeypatch, enabled=True, usernames=["<user>"])
    assert mu_context.get_current_user() is None
    with _user_scope_context("<user>"):
        assert mu_context.get_current_user() == "<user>"
    assert mu_context.get_current_user() is None


def test_cli_backfill_requires_user_in_multiuser_mode(app, monkeypatch):
    "End to end through the click command: no --user, no run."
    _set_mode(monkeypatch, enabled=True, usernames=["<user>"])
    runner = app.test_cli_runner()
    result = runner.invoke(args=["cli", "parent_backfill", "--language", "Japanese"])
    assert result.exit_code != 0
    assert "--user" in result.output


def test_cli_undo_requires_user_in_multiuser_mode(app, monkeypatch):
    _set_mode(monkeypatch, enabled=True, usernames=["<user>"])
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["cli", "parent_backfill_undo", "--audit", "/tmp/none.jsonl"]
    )
    assert result.exit_code != 0
    assert "--user" in result.output
