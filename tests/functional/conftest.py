import os
import pathlib
import shutil
import subprocess

import pytest

import jjx

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
CHARMS_DIR = pathlib.Path(__file__).parent / "charms"


@pytest.fixture(scope="session", autouse=True)
def uv_no_config():
    # Run tests with UV_NO_CONFIG to ignore repo's exclude-newer config.
    # We're doing this because charms are copied to .tmp/ inside our repo.
    # It's a preference to avoid contamination - not needed for correctness.
    # It's heavy-handed, but OK because no test charms have their own uv config.
    old = os.environ.get("UV_NO_CONFIG")
    os.environ["UV_NO_CONFIG"] = "1"
    yield
    if old is None:
        os.environ.pop("UV_NO_CONFIG", None)
    else:
        os.environ["UV_NO_CONFIG"] = old


@pytest.fixture(scope="session", autouse=True)
def system_ready():
    runtime = jjx.container_runtime()
    assert shutil.which(runtime) is not None, f"unable to find {runtime} binary"
    command = [
        runtime,
        "ps",
    ]
    subprocess.run(
        command,
        check=True,
    )
    yield


@pytest.fixture(scope="session", autouse=True)
def cleanup_leaked_containers():
    # Safety net: remove any jjx-managed containers left behind by a test
    # that failed before reaching its TEARDOWN section.
    yield
    runtime = jjx.container_runtime()
    command = [
        runtime,
        "ps",
        "--all",
        "--format",
        "{{.Names}}",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    for name in result.stdout.splitlines():
        name = name.strip()
        if name.startswith(("jubilant-", "jjx-default-", "jjx-layer-copy-")):
            command = [
                runtime,
                "rm",
                "--force",
                name,
            ]
            subprocess.run(
                command,
                capture_output=True,
                check=False,
            )


@pytest.fixture(scope="module")
def temp_dir():
    tmp_dir = PACKAGE_DIR / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / ".gitignore").write_text("*\n")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def k8s_2_configurable(temp_dir):
    charm_dir = temp_dir / "k8s-2-configurable"
    shutil.copytree(
        CHARMS_DIR / "k8s-2-configurable",
        charm_dir,
        ignore=ignore_non_source,
    )
    return charm_dir


def ignore_non_source(_, names: list[str]) -> set[str]:
    return {name for name in names if name.startswith((".", "_")) or name.endswith(".charm")}
