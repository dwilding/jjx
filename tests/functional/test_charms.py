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


def test_charm(package_dir, charm_dir, test_dir):
    working_dir = test_dir / charm_dir.name
    shutil.copytree(charm_dir, working_dir)
    command = [
        "uv",
        "run",
        "--with-editable",
        package_dir,
        "juju",
        "deploy",
    ]
    subprocess.run(
        command,
        cwd=working_dir,
        capture_output=True,
        text=True,
        check=True,
    )
