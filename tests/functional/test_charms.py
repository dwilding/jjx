import pathlib
import shutil
import subprocess

import pytest


def charm_dir_params():
    charms = (pathlib.Path(__file__).parent / "charms").iterdir()
    return [pytest.param(charm, id=charm.name) for charm in charms if charm.is_dir()]


@pytest.fixture(params=charm_dir_params())
def charm_dir(request):
    return request.param


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


def test_charm(package_dir, charm_dir, test_dir):
    working_dir = test_dir / charm_dir.name
    shutil.copytree(charm_dir, working_dir)
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
    subprocess.run(
        command,
        cwd=working_dir,
        capture_output=True,
        text=True,
        check=True,
    )
