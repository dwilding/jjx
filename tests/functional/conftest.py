import pathlib
import shutil
import subprocess

import pytest


@pytest.fixture(scope="session", autouse=True)
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


# Prepare the test directories. The structure is:
#
# .                  package_dir   session scope
# ├── .tmp           temp_dir      module scope
# │   ├── <charm1>   charm_dir     module scope
# │   ├── <charm2>   charm_dir     module scope


@pytest.fixture(scope="session")
def package_dir():
    yield pathlib.Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def temp_dir(package_dir):
    tmp_dir = package_dir / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / ".gitignore").write_text("*\n")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def charm_dir_params():
    charms = (pathlib.Path(__file__).parent / "charms").iterdir()
    return [pytest.param(charm, id=charm.name) for charm in charms if charm.is_dir()]


def ignore_hidden_or_private(_, names):
    return {name for name in names if name.startswith((".", "_"))}


def prepare_charm_dir(source_dir: pathlib.Path, target_dir: pathlib.Path):
    shutil.copytree(
        source_dir,
        target_dir,
        ignore=ignore_hidden_or_private,
    )
    (target_dir / "placeholder.charm").touch()  # "Pack" the charm.


@pytest.fixture(scope="module", params=charm_dir_params())
def charm_dir(temp_dir, request):
    charm_dir = temp_dir / request.param.name
    prepare_charm_dir(request.param, charm_dir)
    return charm_dir
