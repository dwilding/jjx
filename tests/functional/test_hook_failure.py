import pathlib
import re
import subprocess
import time

import jjx
import helpers_functional as helpers

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
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


def test_pebble_ready_crash_sets_error_status(k8s_2_configurable):
    # Patch the charm so the pebble-ready hook crashes.
    charm_src = k8s_2_configurable / "src" / "charm.py"
    match = re.search(
        r"^(\s+)def _on_demo_server_pebble_ready\(.*$", charm_src.read_text(), re.MULTILINE
    )
    assert match is not None
    indent = match.group(1) + "    "
    charm_src.write_text(
        charm_src.read_text().replace(
            match.group(0),
            f'{match.group(0)}\n{indent}raise RuntimeError("deliberate pebble-ready crash for testing")',
        )
    )
    # Clean up any deployed apps from previous tests.
    command = [
        jjx.container_runtime(),
        "rm",
        "--force",
        CONTAINER_NAME,
    ]
    subprocess.run(
        command,
        check=False,
    )
    (k8s_2_configurable / "placeholder.charm").touch()
    # Deploy the app.
    command = [
        *JUJU,
        "deploy",
        "./placeholder.charm",
        "fastapi-demo",
        "--resource",
        "demo-server-image=ghcr.io/canonical/api_demo_server/api-demo-server:2.1.0",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
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
    # wait-for should fail fast with the error message, not time out.
    command = [
        *JUJU,
        "wait-for",
        "application",
        "fastapi-demo",
        "--timeout",
        "30s",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "error state" in result.stderr
    # TEARDOWN
    command = [
        *JUJU,
        "remove-application",
        "fastapi-demo",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            helpers.assert_no_container(CONTAINER_NAME)
            break
        except AssertionError:
            time.sleep(0.5)
    else:
        raise AssertionError("container was not removed")
