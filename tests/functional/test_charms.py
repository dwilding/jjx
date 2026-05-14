import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest


def charm_dir_params():
    charms = (pathlib.Path(__file__).parent / "charms").iterdir()
    return [pytest.param(charm, id=charm.name) for charm in charms if charm.is_dir()]


@pytest.fixture(params=charm_dir_params())
def charm_dir(request):
    return request.param


@pytest.fixture(scope="module", autouse=True)
def test_dir(package_dir):
    root = package_dir / ".tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(tempfile.mkdtemp(dir=root))
    (root / ".gitignore").write_text("*\n")
    # Set up the cached pebble bin directory
    pebble_cache_dir = root / "pebble-bin"
    os.environ["JJX_CACHED_PEBBLE_BIN"] = str(pebble_cache_dir)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def system_ready():
    assert shutil.which("docker") is not None, "docker CLI is not installed"
    assert shutil.which("bwrap") is not None, "bubblewrap (bwrap) is not installed"
    command = [
        "docker",
        "ps",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.strip() or "cannot access docker daemon"


def _ignore_hidden_or_private(_path, names):
    return {name for name in names if name.startswith((".", "_"))}


def test_charm(package_dir, charm_dir, request):
    test_dir = request.getfixturevalue("test_dir")
    working_dir = test_dir / charm_dir.name
    shutil.copytree(
        charm_dir,
        working_dir,
        ignore=_ignore_hidden_or_private,
    )
    (working_dir / "placeholder.charm").touch()  # "Pack" the charm.
    command = [
        "uv",
        "run",
        "--group",
        "integration",
        "--with-editable",
        package_dir,
        "pytest",
        "-v",
        "tests/integration",
    ]
    env = os.environ.copy()
    env["JJX_CACHED_PEBBLE_BIN"] = os.environ.get("JJX_CACHED_PEBBLE_BIN", "")
    subprocess.run(
        command,
        cwd=working_dir,
        env=env,
        check=True,
    )
