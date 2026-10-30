import os
import pathlib
import shutil
import subprocess
import time

import helpers_functional as helpers
import pytest

import jjx

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
CHARMS_DIR = pathlib.Path(__file__).parent / "charms"
JUJU = [
    "uv",
    "run",
    "--group",
    "integration",
    "--with-editable",
    PACKAGE_DIR,
    "juju",
]
CONTAINER_NAME = "jjx-default-demo-server"
DEPLOY = [
    "deploy",
    "./placeholder.charm",
    "fastapi-demo",
    "--resource",
    "demo-server-image=ghcr.io/canonical/api_demo_server/api-demo-server:2.1.0",
]


@pytest.fixture
def charm_dir():
    # Copy the charm under the package tree, not /tmp (see AGENTS.md). Each test
    # mutates charmcraft.yaml, so it needs its own copy.
    root = PACKAGE_DIR / ".tmp" / "test_uid_gid"
    shutil.rmtree(root, ignore_errors=True)
    charm = root / "k8s-2-configurable"
    shutil.copytree(
        CHARMS_DIR / "k8s-2-configurable",
        charm,
        ignore=shutil.ignore_patterns(".*", "_*", "*.charm"),
    )
    return charm


def read_uid_gid(charm_dir: pathlib.Path) -> tuple[str | None, str | None]:
    path = charm_dir / "charmcraft.yaml"
    text = path.read_text()
    uid = None
    gid = None
    in_containers = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "containers:":
            in_containers = True
            continue
        if in_containers and not line.startswith(" "):
            break
        if in_containers and stripped.startswith("uid:"):
            uid = stripped.split(":", 1)[1].strip()
        if in_containers and stripped.startswith("gid:"):
            gid = stripped.split(":", 1)[1].strip()
    return uid, gid


def set_uid_gid(charm_dir: pathlib.Path, uid: int, gid: int) -> None:
    path = charm_dir / "charmcraft.yaml"
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    out = []
    in_containers = False
    resource_done = False
    for line in lines:
        if line.strip() == "containers:":
            in_containers = True
            out.append(line)
            continue
        if in_containers and not line.startswith(" "):
            in_containers = False
            out.append(line)
            continue
        if in_containers and line.strip().startswith("resource:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(line)
            out.append(f"{indent}uid: {uid}\n")
            out.append(f"{indent}gid: {gid}\n")
            resource_done = True
            continue
        out.append(line)
    assert resource_done, "did not find the container resource line to extend"
    path.write_text("".join(out))


def deploy(charm_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    (charm_dir / "placeholder.charm").touch()
    command = [*JUJU, *DEPLOY]
    return subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def wait_for_active(charm_dir: pathlib.Path) -> None:
    # Wait for the container to be running.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            helpers.assert_container(CONTAINER_NAME)
        except subprocess.CalledProcessError:
            time.sleep(0.5)
            continue
        break
    else:
        raise AssertionError("container did not start")
    # Wait for the charm to be active. This happens after pebble-ready fires.
    command = [
        *JUJU,
        "wait-for",
        "application",
        "fastapi-demo",
        "--timeout",
        "30s",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )


def service_user_uid(service: str) -> str:
    # Ask Pebble for the service state, then resolve the numeric UID with
    # docker top (the chiseled image has no ps).
    runtime = jjx.container_runtime()
    result = subprocess.run(
        [
            runtime,
            "exec",
            CONTAINER_NAME,
            "/charm/bin/pebble",
            "services",
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PEBBLE": "/jjx/pebble",
            "PEBBLE_SOCKET": "/jjx/socket",
        },
        check=False,
    )
    assert result.returncode == 0, (
        f"pebble services exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The service must be up for its user to be observable.
    line = next(line for line in result.stdout.splitlines() if line.startswith(service))
    current = line.split()[2] if len(line.split()) > 2 else ""
    assert current == "active", f"service {service} is {current!r}, expected 'active'"
    return _process_uid(runtime)


def _process_uid(runtime: str) -> str:
    # Read the uvicorn process UID with docker top, requesting the args column
    # so the uvicorn line is identifiable.
    result = subprocess.run(
        [
            runtime,
            "top",
            CONTAINER_NAME,
            "-o",
            "pid,uid,args",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"'{runtime} top' exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "uvicorn" in result.stdout, f"no uvicorn process found in:\n{result.stdout}"
    for line in result.stdout.splitlines()[1:]:
        if "uvicorn" not in line:
            continue
        # Columns: PID UID <command...>
        return line.split()[1]
    raise AssertionError(f"no uvicorn line found in:\n{result.stdout}")


def remove_app(charm_dir: pathlib.Path) -> None:
    command = [
        *JUJU,
        "remove-application",
        "fastapi-demo",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_uid_gid_runs_as_image_user(charm_dir):
    # Backstop: the stored charm declares no uid/gid.
    assert read_uid_gid(charm_dir) == (None, None)
    # Deploy the app.
    result = deploy(charm_dir)
    try:
        assert result.returncode == 0, (
            f"juju deploy exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        wait_for_active(charm_dir)
        # The image declares user 584792 (_daemon_), so the service runs as it.
        uid = _process_uid(jjx.container_runtime())
        assert uid == "584792", f"service runs as UID {uid}, expected 584792 (_daemon_)"
        # TEARDOWN
        remove_app(charm_dir)
    finally:
        # Safety net: remove the app in case an assertion failed
        # before TEARDOWN completed.
        remove_app(charm_dir)


def test_uid_gid_zero_runs_as_root(charm_dir):
    # Declare uid/gid 0 (the supervisor shape: root Pebble daemon).
    set_uid_gid(charm_dir, 0, 0)
    # Deploy the app.
    result = deploy(charm_dir)
    try:
        assert result.returncode == 0, (
            f"juju deploy exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        wait_for_active(charm_dir)
        # With --user 0:0 the Pebble daemon, and its services, run as root.
        uid = _process_uid(jjx.container_runtime())
        assert uid == "0", f"service runs as UID {uid}, expected 0 (root)"
        # TEARDOWN
        remove_app(charm_dir)
    finally:
        # Safety net: remove the app in case an assertion failed
        # before TEARDOWN completed.
        remove_app(charm_dir)
