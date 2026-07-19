import subprocess

import jjx


def assert_container(container_name: str) -> None:
    command = [
        jjx.container_runtime(),
        "inspect",
        container_name,
    ]
    subprocess.run(
        command,
        check=True,
    )


def assert_no_container(container_name: str) -> None:
    command = [
        jjx.container_runtime(),
        "inspect",
        container_name,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    # Docker returns 1. Podman returns 125.
    assert result.returncode != 0, f"container {container_name} still exists"


def container_ip(container_name: str) -> str:
    command = [
        jjx.container_runtime(),
        "inspect",
        "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        container_name,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"inspect exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip()
