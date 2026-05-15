import pathlib
import time
import subprocess

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


def assert_one_process() -> None:
    command = [
        "docker",
        "exec",
        "jjx-default-fastapi-demo",
        "ps",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.count("uvicorn") == 1


def test_server_process(k8s_2_configurable):
    # Clean up any deployed apps from previous tests.
    command = [
        "docker",
        "rm",
        "--force",
        "jjx-default-fastapi-demo",
    ]
    subprocess.run(
        command,
        check=False,
    )
    # Deploy the app.
    command = [
        *JUJU,
        "deploy",
        "./placeholder.charm",
        "fastapi-demo",
        "--resource",
        "demo-server-image=ghcr.io/canonical/api_demo_server:1.0.3",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    # Wait for the app to be active.
    command = [
        *JUJU,
        "wait-for",
        "application",
        "fastapi-demo",
        "--timeout",
        "2s",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    # Check that there's one server process in the container.
    assert_one_process()


def assert_server_state(port: int, expect_up: bool) -> None:
    code = (
        "import sys, urllib.request;"
        f"url = 'http://127.0.0.1:{str(port)}/version';"
        "response = urllib.request.urlopen(url, timeout=2);"
        "assert response.status == 200"
    )
    command = [
        "docker",
        "exec",
        "jjx-default-fastapi-demo",
        "python3",
        "-c",
        code,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == expect_up


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
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    port = int(result.stdout.strip())
    assert_server_state(port, True)
    # Bump the port.
    command = [
        *JUJU,
        "config",
        "fastapi-demo",
        f"server-port={str(port + 1)}",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    time.sleep(2)
    # Check that there's still one server process in the container.
    assert_one_process()
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
    time.sleep(2)
    # Check that the container doesn't exist.
    command = [
        "docker",
        "inspect",
        "jjx-default-fastapi-demo",
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
