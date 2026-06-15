import os
import pathlib
import signal
import subprocess
import time
import urllib.error
import urllib.request

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def wait_for_output_line(proc: subprocess.Popen[str], text: str) -> str:
    deadline = time.time() + 10
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
    (k8s_2_configurable / "placeholder.charm").unlink()  # .charm file was created by the fixture.
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
        assert_no_jjx_in_charm_venv(k8s_2_configurable)
        # Teardown
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=5) == 130
        assert proc.stdout is not None
        assert f"Stopped {container_name}" in proc.stdout.read()
        assert_no_container(container_name)
        assert not (k8s_2_configurable / ".jjx").exists()
        assert not (k8s_2_configurable / "placeholder.charm").exists()
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
        # Teardown
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=5) == 130
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
        # Teardown
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=5) == 130
    finally:
        if proc.poll() is None:
            proc.kill()


def test_jjx_down(k8s_2_configurable):
    (k8s_2_configurable / "placeholder.charm").touch()
    command = [
        "uv",
        "run",
        "--group",
        "integration",
        "pytest",
        "-v",
        "tests/integration",
        "--no-juju-teardown",
    ]  # jjx is already installed, from a previous test.
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
        capture_output=True,
        text=True,
    )
    model_name = result.stdout.split("--juju-model ")[1].split()[0]
    container_name = f"{model_name}-test-charm-fastapi-demo"
    assert_container(container_name)
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
    assert f"Stopped {container_name}" in result.stdout
    assert_no_container(container_name)
    assert not (k8s_2_configurable / ".jjx").exists()
    assert (k8s_2_configurable / "placeholder.charm").exists()  # 'jjx down' ignores .charm files.


def test_jjx_no_deploy(k8s_2_configurable):
    test_file = k8s_2_configurable / "tests" / "integration" / "test_charm.py"
    code = test_file.read_text()
    code = code.replace("juju.deploy", "juju.dont_deploy")  # Break the charm's integration tests.
    test_file.write_text(code)
    command = [
        "uv",
        "run",
        "jjx",
    ]  # jjx is already installed, from a previous test.
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Started workload container " not in result.stdout
