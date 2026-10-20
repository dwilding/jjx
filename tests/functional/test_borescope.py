import pathlib
import subprocess

import helpers_functional as helpers

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
BORESCOPE = [
    "uvx",
    # CI pins UV_PYTHON=3.10, but borescope requires Python 3.11+.
    "--python",
    "3.11",
    "borescope",
    "--socket",
    ".jjx/socket",
]


def test_borescope_probes_workload(k8s_2_configurable):
    # Start the workload detached.
    # The containers stay up after jjx exits, so borescope can probe them.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        assert result.returncode == 0, (
            f"jjx exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Workload running at " in result.stdout
        model_name = result.stdout.split("--juju-model ")[1].split()[0]
        container_name = f"{model_name}-test-charm-demo-server"
        operator_container_name = f"{model_name}-test-charm-operator"
        helpers.assert_container(container_name)
        helpers.assert_container(operator_container_name)
        # Probe the workload's services with borescope over the Pebble socket.
        # This is the usage documented in the README.
        command = [
            *BORESCOPE,
            "--command",
            "services",
        ]
        result = subprocess.run(
            command,
            cwd=k8s_2_configurable,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"borescope services exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "fastapi" in result.stdout
        assert "active" in result.stdout
        # Check that borescope can see the workload process.
        command = [
            *BORESCOPE,
            "--command",
            "ps",
        ]
        result = subprocess.run(
            command,
            cwd=k8s_2_configurable,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"borescope ps exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "uvicorn" in result.stdout
        # TEARDOWN
        command = [
            "uvx",
            "--with-editable",
            PACKAGE_DIR,
            "jjx",
            "down",
        ]
        result = subprocess.run(
            command,
            cwd=k8s_2_configurable,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"jjx down exited with code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert f"Removed {container_name}" in result.stdout
        assert f"Removed {operator_container_name}" in result.stdout
        helpers.assert_no_container(container_name)
        helpers.assert_no_container(operator_container_name)
        assert not (k8s_2_configurable / ".jjx").exists()
    finally:
        # Safety net for jjx -d: run jjx down in case an assertion
        # failed before TEARDOWN completed.
        command = [
            "uvx",
            "--with-editable",
            PACKAGE_DIR,
            "jjx",
            "down",
        ]
        subprocess.run(
            command,
            cwd=k8s_2_configurable,
            capture_output=True,
            text=True,
            check=False,
        )
