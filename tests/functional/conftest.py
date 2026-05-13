import pathlib

import pytest


@pytest.fixture(scope="session")
def package_dir():
    yield pathlib.Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def test_dir(tmp_path_factory):
    yield tmp_path_factory.mktemp("functional")
