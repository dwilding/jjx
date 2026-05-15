import pathlib
import shutil
import subprocess

import pytest

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
CHARMS_DIR = pathlib.Path(__file__).parent / "charms"
CHARM_PARAMS = [pytest.param(dir, id=dir.name) for dir in CHARMS_DIR.iterdir() if dir.is_dir()]


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
    )
    assert result.returncode == 0, result.stderr.strip() or "cannot access docker daemon"


@pytest.fixture(scope="module")
def temp_dir():
    tmp_dir = PACKAGE_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / ".gitignore").write_text("*\n")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


def ignore_hidden_or_private(_, names):
    return {name for name in names if name.startswith((".", "_"))}


def prepare_charm_dir(source_dir: pathlib.Path, target_dir: pathlib.Path):
    shutil.copytree(
        source_dir,
        target_dir,
        ignore=ignore_hidden_or_private,
    )
    (target_dir / "placeholder.charm").touch()  # "Pack" the charm.


@pytest.fixture(scope="module", params=CHARM_PARAMS)
def charm_dir(temp_dir, request):
    charm_dir = temp_dir / request.param.name
    prepare_charm_dir(request.param, charm_dir)
    return charm_dir


@pytest.fixture(scope="module")
def k8s_2_configurable(temp_dir):
    charm_dir = temp_dir / "k8s-2-configurable"
    prepare_charm_dir(CHARMS_DIR / "k8s-2-configurable", charm_dir)
    return charm_dir
