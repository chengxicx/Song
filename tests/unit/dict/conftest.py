"""
Fixtures for the dictionary tests.
"""

import os
import shutil
import tempfile

import pytest


@pytest.fixture(name="dict_dirs")
def fixture_dict_dirs(app, monkeypatch):
    """
    Point the app's static folder at a scratch directory, and hand back
    (static, scratch): the dictionary is published under static, and
    scratch holds the .mdx files the tests "upload".

    Not pytest's tmp_path: the sandbox this repo is developed in cannot
    create directories under the system temp root.
    """
    root = tempfile.mkdtemp(dir=os.path.dirname(__file__))
    static_dir = os.path.join(root, "static")
    scratch_dir = os.path.join(root, "scratch")
    os.makedirs(static_dir)
    os.makedirs(scratch_dir)
    monkeypatch.setattr(app, "static_folder", static_dir)
    yield static_dir, scratch_dir
    shutil.rmtree(root, ignore_errors=True)
