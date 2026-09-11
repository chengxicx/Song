"""
Multi-user administration CLI.

Run on the server, e.g.:

    python -m lute.multiuser list-users
    python -m lute.multiuser reset-password admin
    python -m lute.multiuser reset-password admin --password newpass

Resetting a password changes users.db directly and takes effect
immediately, without restarting the server.
"""

import argparse
import os
import sys
from getpass import getpass

from lute.config.app_config import AppConfig
from lute.multiuser import store


def _resolve_config(config_path):
    "Same config resolution order as lute.main."
    if config_path:
        return config_path
    if os.path.exists("config.yml"):
        return "config.yml"
    return AppConfig.default_config_filename()


def _load(config_path):
    app_config = AppConfig(_resolve_config(config_path))
    store.load(app_config)
    if not os.path.exists(store.users_dbfile()):
        print(
            f"No users.db found in {app_config.datapath} -- multi-user mode has never been enabled."
        )
        sys.exit(1)
    return app_config


def cmd_list_users(_args):
    _load(_args.config)
    rows = store.users()
    if not rows:
        print("No users.")
        return
    print(f"{'Username':<32} {'Role':<8} Created")
    for u in rows:
        print(f"{u['username']:<32} {u['role']:<8} {u['created_at']}")


def cmd_reset_password(args):
    _load(args.config)
    if not store.user_exists(args.username):
        print(f"User '{args.username}' does not exist.  Use list-users to check.")
        sys.exit(1)
    if args.password:
        new_password = args.password
    else:
        new_password = getpass("New password: ")
        confirm = getpass("Confirm password: ")
        if new_password != confirm:
            print("Passwords do not match.")
            sys.exit(1)
    try:
        store.set_password(args.username, new_password)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Password for '{args.username}' was changed.  It takes effect immediately.")


def main():
    parser = argparse.ArgumentParser(
        prog="python -m lute.multiuser",
        description="Administer Lute multi-user accounts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_config_arg(p):
        # Accepted both before and after the subcommand.
        p.add_argument(
            "--config",
            help="Path to config.yml (defaults to ./config.yml or the packaged config).",
        )

    _add_config_arg(parser)
    p_list = sub.add_parser("list-users", help="List user accounts.")
    _add_config_arg(p_list)
    p_list.set_defaults(func=cmd_list_users)

    p_reset = sub.add_parser("reset-password", help="Reset a user's password.")
    p_reset.add_argument("username")
    p_reset.add_argument(
        "--password",
        help="New password (omit to be prompted securely).",
    )
    _add_config_arg(p_reset)
    p_reset.set_defaults(func=cmd_reset_password)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
