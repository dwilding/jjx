import os
import pathlib
import signal
import subprocess
import time
import urllib.error
import urllib.request

import helpers_functional as helpers

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def wait_for_output_line(proc: subprocess.Popen[str], text: str) -> str:
    assert proc.stdout is not None
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        if text in line:
            return line.strip()
    raise AssertionError(f"did not find {text!r} in process output before timeout")


def assert_no_jjx_in_charm_venv(charm_dir: pathlib.Path) -> None:
    command = [
        charm_dir / ".venv" / "bin" / "python",
        "-c",
        "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('jjx') is None else 1)",
    ]
    subprocess.run(
        command,
        check=True,
    )


def assert_connection(url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=1):
            pass
    except urllib.error.URLError:
        raise AssertionError(f"expected connection to succeed for {url}")


def assert_no_connection(url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=1):
            pass
    except urllib.error.URLError:
        return
    raise AssertionError(f"expected connection to fail for {url}")


def test_uvx_jjx(k8s_2_configurable):
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
    ]
    proc = subprocess.Popen(
        command,
        cwd=k8s_2_configurable,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        model_line = wait_for_output_line(proc, "--juju-model ")
        model_name = model_line.split("--juju-model ")[1].split()[0]
        container_name = f"{model_name}-test-charm-demo-server"
        operator_container_name = f"{model_name}-test-charm-operator"
        status_line = wait_for_output_line(proc, "Workload running at ")
        _, _, container_ip = status_line.partition("Workload running at ")
        assert container_ip
        assert_connection(f"http://{container_ip}:8000")
        assert_no_connection("http://127.0.0.1:8000")
        assert not (k8s_2_configurable / "placeholder.charm").exists()
        assert_no_jjx_in_charm_venv(k8s_2_configurable)
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
        assert proc.stdout is not None
        output = proc.stdout.read()
        assert f"Removed {container_name}" in output
        assert f"Removed {operator_container_name}" in output
        helpers.assert_no_container(container_name)
        helpers.assert_no_container(operator_container_name)
        assert not (k8s_2_configurable / ".jjx").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_uvx_jjx_publish(k8s_2_configurable):
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "-p",
        "8135:8000",
    ]
    proc = subprocess.Popen(
        command,
        cwd=k8s_2_configurable,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_output_line(proc, "Workload running at 127.0.0.1:8135")
        assert_connection("http://127.0.0.1:8135")
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
    finally:
        if proc.poll() is None:
            proc.kill()


def test_uv_run_jjx(k8s_2_configurable):
    command = [
        "uv",
        "pip",
        "install",
        "--editable",
        PACKAGE_DIR,
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    command = [
        "uv",
        "run",
        "jjx",
    ]
    proc = subprocess.Popen(
        command,
        cwd=k8s_2_configurable,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_output_line(proc, "Workload running at ")
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
    finally:
        if proc.poll() is None:
            proc.kill()


def test_jjx_detach_then_down(k8s_2_configurable):
    command = [
        "uv",
        "run",
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
            f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Workload running at " in result.stdout
        model_name = result.stdout.split("--juju-model ")[1].split()[0]
        container_name = f"{model_name}-test-charm-demo-server"
        operator_container_name = f"{model_name}-test-charm-operator"
        helpers.assert_container(container_name)
        helpers.assert_container(operator_container_name)
        assert not (k8s_2_configurable / "placeholder.charm").exists()
        # TEARDOWN
        command = [
            "uv",
            "run",
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
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
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
            "uv",
            "run",
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


def test_jjx_detach_then_rerun(k8s_2_configurable):
    command = [
        "uv",
        "run",
        "jjx",
        "-d",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        assert result.returncode == 1
        assert "Workload running at " not in result.stdout
        assert " is up" in result.stderr
        assert (k8s_2_configurable / ".jjx").exists()
        # TEARDOWN
        command = [
            "uv",
            "run",
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
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert result.stdout.count("Removed ") == 2
    finally:
        # Safety net for jjx -d: run jjx down in case an assertion
        # failed before TEARDOWN completed.
        command = [
            "uv",
            "run",
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


def test_jjx_pytest_fail(k8s_2_configurable):
    # Add a failing integration test.
    test_charm = k8s_2_configurable / "tests" / "integration" / "test_charm.py"
    test_charm.write_text(
        test_charm.read_text()
        + '\n\ndef test_always_fails():\n    raise AssertionError("deliberate failure")\n'
    )
    command = [
        "uv",
        "run",
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
        assert result.returncode == 1
        # The container should still be running because `test_deploy` should have passed.
        assert "Workload running at " in result.stdout
        assert (k8s_2_configurable / ".jjx").exists()
        # TEARDOWN
        command = [
            "uv",
            "run",
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
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Removed " in result.stdout
    finally:
        # Safety net for jjx -d: run jjx down in case an assertion
        # failed before TEARDOWN completed.
        command = [
            "uv",
            "run",
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


def test_jjx_pytest_select(k8s_2_configurable):
    pytest_extra_args = '["-k", "test_deploy"]'
    pyproject = k8s_2_configurable / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + f"\n[tool.jjx]\npytest-extra-args = {pytest_extra_args}\n"
    )
    command = [
        "uv",
        "run",
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
            f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Workload running at " in result.stdout
        assert (k8s_2_configurable / ".jjx").exists()
        # TEARDOWN
        command = [
            "uv",
            "run",
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
            f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "Removed " in result.stdout
    finally:
        # Safety net for jjx -d: run jjx down in case an assertion
        # failed before TEARDOWN completed.
        command = [
            "uv",
            "run",
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


def test_jjx_pytest_select_verbose(k8s_2_configurable):
    command = [
        "uv",
        "run",
        "jjx",
        "-d",
        "--",
        "-vv",
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
            f"jjx exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # -vv makes pytest print each test node id with a PASSED/FAILED marker.
        assert "test_charm.py::test_deploy" in result.stdout
        # TEARDOWN
        command = [
            "uv",
            "run",
            "jjx",
            "down",
        ]
        subprocess.run(
            command,
            cwd=k8s_2_configurable,
            check=True,
        )
    finally:
        # Safety net for jjx -d: run jjx down in case an assertion
        # failed before TEARDOWN completed.
        command = [
            "uv",
            "run",
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


def test_jjx_no_deploy(k8s_2_configurable):
    # Break the integration test that deploys the charm.
    test_charm = k8s_2_configurable / "tests" / "integration" / "test_charm.py"
    test_charm.write_text(test_charm.read_text().replace("juju.deploy", "juju.dont_deploy"))
    command = [
        "uv",
        "run",
        "jjx",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Workload running at " not in result.stdout
    model_name = result.stdout.split("--juju-model ")[1].split()[0]
    helpers.assert_no_container(f"{model_name}-test-charm-operator")
    helpers.assert_no_container(f"{model_name}-test-charm-demo-server")
    assert not (k8s_2_configurable / ".jjx").exists()
