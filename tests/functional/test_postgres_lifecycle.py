import json
import pathlib
import subprocess
import urllib.request

import helpers_functional as helpers

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


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
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    model_name = result.stdout.split("--juju-model ")[1].split()[0]
    container_name = f"{model_name}-test-charm-fastapi-demo"
    operator_container_name = f"{model_name}-test-charm-operator"
    postgres_container_name = f"{model_name}-test-charm-postgres"
    helpers.assert_container(container_name)
    helpers.assert_container(operator_container_name)
    helpers.assert_container(postgres_container_name)
    # Check that the workload can talk to the database.
    # This isn't covered by the charm's integration tests.
    api_base = "http://127.0.0.1:8135"
    response = urllib.request.urlopen(f"{api_base}/names", timeout=10)
    assert json.loads(response.read()) == {"names": {}}
    urllib.request.urlopen(
        urllib.request.Request(
            f"{api_base}/addname/",
            data=b"name=elephant",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        ),
        timeout=10,
    )
    response = urllib.request.urlopen(f"{api_base}/names", timeout=10)
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
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert f"Removed {container_name}" in result.stdout
    assert f"Removed {operator_container_name}" in result.stdout
    assert f"Removed {postgres_container_name}" in result.stdout
    helpers.assert_no_container(container_name)
    helpers.assert_no_container(operator_container_name)
    helpers.assert_no_container(postgres_container_name)
