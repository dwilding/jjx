import os
import pathlib
import signal
import subprocess
import time
import urllib.error
import urllib.request

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def wait_for_output_line(proc: subprocess.Popen[str], text: str) -> str:
    deadline = time.time() + 20
    assert proc.stdout is not None
    while time.time() < deadline:
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
        status_line = wait_for_output_line(proc, "Started workload container ")
        _, _, rest = status_line.partition("Started workload container ")
        container_name, _, container_ip = rest.partition(" with IP ")
        assert container_name.endswith("-fastapi-demo")
        assert container_ip
        assert_connection(f"http://{container_ip}:8000")
        assert_no_connection("http://127.0.0.1:8000")
        assert not (k8s_2_configurable / "placeholder.charm").exists()
        assert_no_jjx_in_charm_venv(k8s_2_configurable)
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 130
        assert proc.stdout is not None
        assert f"Removed container {container_name}" in proc.stdout.read()
        assert_no_container(container_name)
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
        wait_for_output_line(proc, "Published container port 8000 to 127.0.0.1:8135")
        assert_connection("http://127.0.0.1:8135")
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 130
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
        wait_for_output_line(proc, "Started workload container ")
        # TEARDOWN
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 130
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
        check=True,
        capture_output=True,
        text=True,
    )
    model_name = result.stdout.split("--juju-model ")[1].split()[0]
    container_name = f"{model_name}-test-charm-fastapi-demo"
    assert f"Started workload container {container_name}" in result.stdout
    assert_container(container_name)
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
        check=True,
        capture_output=True,
        text=True,
    )
    assert f"Removed container {container_name}" in result.stdout
    assert_no_container(container_name)
    assert not (k8s_2_configurable / ".jjx").exists()


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
    )
    assert result.returncode == 1
    assert "Started workload container " not in result.stdout
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
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.count("Removed container ") == 1


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
    )
    assert result.returncode == 1
    # The container should still be running because `test_deploy` should have passed.
    assert "Started workload container " in result.stdout
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
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Removed container " in result.stdout


def test_jjx_pytest_select_and_teardown(k8s_2_configurable):
    pytest_args = '["tests/integration", "-k", "test_deploy"]'  # Dropped --no-juju-teardown.
    pyproject = k8s_2_configurable / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + f"\n[tool.jjx]\npytest-args = {pytest_args}\n")
    command = [
        "uv",
        "run",
        "jjx",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Started workload container " not in result.stdout
    assert not (k8s_2_configurable / ".jjx").exists()


def test_jjx_no_deploy(k8s_2_configurable):
    # Restore --no-juju-teardown.
    pyproject = k8s_2_configurable / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace('"test_deploy"', '"test_deploy", "--no-juju-teardown"')
    )
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
    )
    assert result.returncode == 1
    assert "Started workload container " not in result.stdout
    assert not (k8s_2_configurable / ".jjx").exists()
