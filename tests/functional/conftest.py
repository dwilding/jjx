import pathlib
import shutil
import tempfile

import pytest


@pytest.fixture(scope="session")
def package_dir():
    yield pathlib.Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def test_dir(package_dir):
    root = package_dir / ".tmp-functional"
    root.mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(tempfile.mkdtemp(prefix="functional-", dir=root))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
