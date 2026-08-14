import pathlib
import subprocess
import time
import urllib.request

import helpers_functional as helpers

import jjx

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


def assert_process_count(count: int) -> None:
    runtime = jjx.container_runtime()
    command = [
        runtime,
        "top",
        CONTAINER_NAME,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"'{runtime} top' exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.count("uvicorn") == count


def assert_server_state(port: int, expect_up: bool) -> None:
    assert server_up(helpers.container_ip(CONTAINER_NAME), port) == expect_up


def server_up(ip: str, port: int) -> bool:
    url = f"http://{ip}:{port}/version"
    try:
        response = urllib.request.urlopen(url, timeout=2)
    except Exception:  # noqa: BLE001
        return False
    return response.status == 200


def test_container_processes(k8s_2_configurable):
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
    # Check that there are no server processes yet. pebble-ready fires after a delay.
    assert_process_count(0)
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
        cwd=k8s_2_configurable,
        check=True,
    )
    # Check that there's now one server process.
    assert_process_count(1)


def test_pebble_was_unreachable(k8s_2_configurable):
    command = [
        *JUJU,
        "debug-log",
        "--limit",
        "100",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"juju debug-log exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Check that Pebble was unreachable when handling config-changed, as with real Juju.
    # The log message comes from _replan_workload(), which is called on config-changed and pebble-ready.
    assert "Unable to connect to Pebble" in result.stdout


def test_pebble_services():
    runtime = jjx.container_runtime()
    command = [
        runtime,
        "exec",
        CONTAINER_NAME,
        "/charm/bin/pebble",
        "services",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"'{runtime} exec' exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "fastapi" in result.stdout


def test_pebble_logs():
    runtime = jjx.container_runtime()
    command = [
        runtime,
        "exec",
        CONTAINER_NAME,
        "/charm/bin/pebble",
        "logs",
        "fastapi",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"'{runtime} exec' exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Uvicorn running" in result.stdout


def test_server_changes_port(k8s_2_configurable):
    # Check that the server responds on the currently-configured port.
    command = [
        *JUJU,
        "config",
        "fastapi-demo",
        "server-port",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"juju config exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    port = int(result.stdout.strip())
    assert_server_state(port, True)
    # Bump the port.
    command = [
        *JUJU,
        "config",
        "fastapi-demo",
        f"server-port={port + 1!s}",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if server_up(helpers.container_ip(CONTAINER_NAME), port + 1):
            break
        time.sleep(0.5)
    else:
        raise AssertionError(f"unable to reach server on port {port + 1}")
    # Check that there's still one server process in the container.
    assert_process_count(1)
    # Check that the server doesn't respond on the old port.
    assert_server_state(port, False)
    # Check that the server responds on the new port.
    assert_server_state(port + 1, True)


def test_teardown_container(k8s_2_configurable):
    # Remove the app.
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
    # Check that the container doesn't exist.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            helpers.assert_no_container(CONTAINER_NAME)
            break
        except AssertionError:
            time.sleep(0.5)
    else:
        raise AssertionError("container was not removed")
