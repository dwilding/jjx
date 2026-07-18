import pathlib
import time
import subprocess
import urllib.request

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


def assert_container() -> None:
    command = [
        "docker",
        "inspect",
        "jjx-default-fastapi-demo",
    ]
    subprocess.run(
        command,
        check=True,
    )


def assert_no_container() -> None:
    command = [
        "docker",
        "inspect",
        "jjx-default-fastapi-demo",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def assert_process_count(count: int) -> None:
    command = [
        "docker",
        "top",
        "jjx-default-fastapi-demo",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docker top exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout.count("uvicorn") == count


def assert_server_state(port: int, expect_up: bool) -> None:
    assert server_up(get_container_ip(), port) == expect_up


def server_up(ip: str, port: int) -> bool:
    url = f"http://{ip}:{port}/version"
    try:
        response = urllib.request.urlopen(url, timeout=2)
    except Exception:
        return False
    return response.status == 200


def get_container_ip() -> str:
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            "jjx-default-fastapi-demo",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docker inspect exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip()


def test_container_processes(k8s_2_configurable):
    # Clean up any deployed apps from previous tests.
    command = [
        "docker",
        "rm",
        "--force",
        "jjx-default-fastapi-demo",
    ]
    subprocess.run(
        command,
        check=False,  # Ignore a missing container (good enough for now).
    )
    (k8s_2_configurable / "placeholder.charm").touch()
    # Deploy the app.
    command = [
        *JUJU,
        "deploy",
        "./placeholder.charm",
        "fastapi-demo",
        "--resource",
        "demo-server-image=ghcr.io/canonical/api_demo_server:1.0.4",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    # Wait for the container to be running.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            assert_container()
        except subprocess.CalledProcessError:
            time.sleep(0.5)
            continue
        break
    else:
        raise AssertionError("container did not start")
    # Check that there are no server processes (pebble-ready fires after a delay).
    assert_process_count(0)
    # Wait for the charm to be active (happens after pebble-ready fires).
    command = [
        *JUJU,
        "wait-for",
        "application",
        "fastapi-demo",
        "--timeout",
        "10s",
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
    )
    assert result.returncode == 0, (
        f"juju debug-log exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Check that Pebble was unreachable when handling config-changed, as with real Juju.
    # The log message comes from _replan_workload() - called on config-changed and pebble-ready.
    assert "Unable to connect to Pebble" in result.stdout


def test_pebble_services():
    command = [
        "docker",
        "exec",
        "jjx-default-fastapi-demo",
        "/charm/bin/pebble",
        "services",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docker exec exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "fastapi-service" in result.stdout


def test_pebble_logs():
    command = [
        "docker",
        "exec",
        "jjx-default-fastapi-demo",
        "/charm/bin/pebble",
        "logs",
        "fastapi-service",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"docker exec exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
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
        f"server-port={str(port + 1)}",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    time.sleep(2)
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
    time.sleep(2)
    # Check that the container doesn't exist.
    assert_no_container()
