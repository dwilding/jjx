import pathlib
import shutil
import subprocess

import helpers_functional as helpers

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_charmcraft_profile(temp_dir):
    # Generate a charm from Charmcraft.
    charm_dir = temp_dir / "myapp"
    charm_dir.mkdir()
    command = [
        "uvx",
        "--with",
        "git+https://github.com/canonical/charmcraft@main",
        "charmcraft",
        "init",
        "--profile",
        "kubernetes",
        "--author",
        "Charmer",
    ]
    subprocess.run(
        command,
        cwd=charm_dir,
        check=True,
    )
    # Run the charm's integration tests with jjx.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        assert result.returncode == 0, (
            f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        model_name = result.stdout.split("--juju-model ")[1].split()[0]
        container_names = [
            f"{model_name}-test-charm-operator",
            f"{model_name}-test-charm-some-container",
        ]
        for container_name in container_names:
            helpers.assert_container(container_name)
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
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        for container_name in container_names:
            assert f"Removed {container_name}" in result.stdout
            helpers.assert_no_container(container_name)
    finally:
        # Safety net: ensure containers are removed even if an assertion
        # above failed before TEARDOWN completed.
        command = [
            "uvx",
            "--with-editable",
            PACKAGE_DIR,
            "jjx",
            "down",
        ]
        subprocess.run(
            command,
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )


def test_httpbin_demo(temp_dir):
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "https://github.com/canonical/operator.git",
    ]
    subprocess.run(
        command,
        cwd=temp_dir,
        check=True,
    )
    shutil.move(temp_dir / "operator" / "examples" / "httpbin-demo", temp_dir)
    shutil.rmtree(temp_dir / "operator")
    charm_dir = temp_dir / "httpbin-demo"
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        assert result.returncode == 0, (
            f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        model_name = result.stdout.split("--juju-model ")[1].split()[0]
        container_names = [
            f"{model_name}-test-charm-operator",
            f"{model_name}-test-charm-httpbin",
        ]
        for container_name in container_names:
            helpers.assert_container(container_name)
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
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        for container_name in container_names:
            assert f"Removed {container_name}" in result.stdout
            helpers.assert_no_container(container_name)
    finally:
        # Safety net: ensure containers are removed even if an assertion
        # above failed before TEARDOWN completed.
        command = [
            "uvx",
            "--with-editable",
            PACKAGE_DIR,
            "jjx",
            "down",
        ]
        subprocess.run(
            command,
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )


def test_fastapi_demo(temp_dir):
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "https://github.com/dwilding/fastapi-demo-operator.git",
    ]
    subprocess.run(
        command,
        cwd=temp_dir,
        check=True,
    )
    charm_dir = temp_dir / "fastapi-demo-operator"
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-d",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        assert result.returncode == 0, (
            f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Grafana" in result.stdout
        assert "Prometheus" in result.stdout
        assert "Loki API" in result.stdout
        model_name = result.stdout.split("--juju-model ")[1].split()[0]
        container_names = [
            f"{model_name}-test-charm-operator",
            f"{model_name}-test-charm-demo-server",
            f"{model_name}-test-charm-postgres",
            f"{model_name}-test-charm-cos-loki",
            f"{model_name}-test-charm-cos-prometheus",
            f"{model_name}-test-charm-cos-grafana",
        ]
        for container_name in container_names:
            helpers.assert_container(container_name)
        helpers.assert_loki_config(
            loki_ip=helpers.container_ip(f"{model_name}-test-charm-cos-loki"),
            label="charm",
            value="fastapi-demo",
        )
        helpers.assert_prometheus_config(
            prometheus_ip=helpers.container_ip(f"{model_name}-test-charm-cos-prometheus"),
            workload_ip=helpers.container_ip(f"{model_name}-test-charm-demo-server"),
            metric="starlette_requests_total",
        )
        helpers.assert_grafana_config(
            grafana_ip=helpers.container_ip(f"{model_name}-test-charm-cos-grafana"),
            datasources={"prometheusds": "prometheus", "lokids": "loki"},
            dashboard_title="FastAPI Monitoring",
        )
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
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        for container_name in container_names:
            assert f"Removed {container_name}" in result.stdout
            helpers.assert_no_container(container_name)
    finally:
        # Safety net: ensure containers are removed even if an assertion
        # above failed before TEARDOWN completed.
        command = [
            "uvx",
            "--with-editable",
            PACKAGE_DIR,
            "jjx",
            "down",
        ]
        subprocess.run(
            command,
            cwd=charm_dir,
            capture_output=True,
            text=True,
            check=False,
        )


def test_fastapi_demo_deselect_deploy(temp_dir):
    charm_dir = temp_dir / "fastapi-demo-operator"
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "--",
        "--deselect",
        "tests/integration/test_charm.py::test_deploy",
    ]
    result = subprocess.run(
        command,
        cwd=charm_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Workload running at " not in result.stdout
    # jjx prints the model name even when tests fail. If it doesn't, jjx
    # itself crashed and there's nothing to clean up.
    if "--juju-model " in result.stdout:
        model_name = result.stdout.split("--juju-model ")[1].split()[0]
        container_names = [
            f"{model_name}-test-charm-operator",
            f"{model_name}-test-charm-demo-server",
            f"{model_name}-test-charm-postgres",
            f"{model_name}-test-charm-cos-loki",
            f"{model_name}-test-charm-cos-prometheus",
            f"{model_name}-test-charm-cos-grafana",
        ]
        for container_name in container_names:
            helpers.assert_no_container(container_name)
    assert not (charm_dir / ".jjx").exists()
