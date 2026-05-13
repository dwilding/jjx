import pathlib

import pytest


@pytest.fixture(scope="session")
def package_dir():
    yield pathlib.Path(__file__).parent.parent.parent
