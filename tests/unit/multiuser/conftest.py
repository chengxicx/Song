"""
Fixtures for multi-user tests: a throwaway datapath + config.yml,
an app created from it, and helpers to drive multi-user mode.
"""

import pytest

from lute.app_factory import create_app
from lute.multiuser import context, paths, store, switching


@pytest.fixture(name="mu_datapath")
def fixture_mu_datapath(tmp_path):
    "A clean datapath + config file for a multi-user test app."
    datapath = tmp_path / "data"
    datapath.mkdir()
    cfgfile = tmp_path / "config.yml"
    cfgfile.write_text(
        f"ENV: dev\nDBNAME: test_mu.db\nDATAPATH: {datapath}\n", encoding="utf-8"
    )
    return str(cfgfile), str(datapath)


@pytest.fixture(name="mu_app")
def fixture_mu_app(mu_datapath):
    "A single-user-mode app on a clean datapath."
    cfgfile, datapath = mu_datapath
    app = create_app(cfgfile, extra_config={"TESTING": True})
    app.test_datapath = datapath
    return app


@pytest.fixture(name="mu_enabled_app")
def fixture_mu_enabled_app(mu_app):
    "App with multi-user mode enabled; admin 'admin' / 'pass1234'."
    base = mu_app.env_config.base_config
    switching.enable_fresh(base, "admin", "pass1234")
    return mu_app


@pytest.fixture(name="mu_client")
def fixture_mu_client(mu_enabled_app):
    "Test client + a login helper."
    client = mu_enabled_app.test_client()

    def login(username, password):
        return client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )

    client.login = login
    return client


def user_config_for(app, username):
    return paths.user_config(app.env_config.base_config, username)
