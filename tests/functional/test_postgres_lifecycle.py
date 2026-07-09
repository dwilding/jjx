import json
import pathlib
import subprocess
import urllib.request

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def assert_container(container_name: str) -> None:
    command = [
        "docker",
        "inspect",
        container_name,
    ]
    subprocess.run(
        command,
        check=True,
    )


def assert_no_container(container_name: str) -> None:
    command = [
        "docker",
        "inspect",
        container_name,
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1


def test_charm_with_postgres(k8s_4_action):
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
        "-p",
        "8135:8000",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_4_action,
        check=True,
        capture_output=True,
        text=True,
    )
    model_name = result.stdout.split("--juju-model ")[1].split()[0]
    container_name = f"{model_name}-test-charm-fastapi-demo"
    postgres_container_name = f"{model_name}-test-charm-postgres"
    assert_container(container_name)
    assert_container(postgres_container_name)
    # Check that the workload can talk to the database.
    # This isn't covered by the charm's integration tests.
    api_base = "http://127.0.0.1:8135"
    response = urllib.request.urlopen(f"{api_base}/names")
    assert json.loads(response.read()) == {"names": {}}
    urllib.request.urlopen(
        urllib.request.Request(
            f"{api_base}/addname/",
            data=b"name=elephant",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
    )
    response = urllib.request.urlopen(f"{api_base}/names")
    assert json.loads(response.read()) == {"names": {"1": "elephant"}}
    # Tear down both containers.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "down",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_4_action,
        check=True,
        capture_output=True,
        text=True,
    )
    assert f"Removed container {container_name}" in result.stdout
    assert f"Removed container {postgres_container_name}" in result.stdout
    assert_no_container(container_name)
    assert_no_container(postgres_container_name)
