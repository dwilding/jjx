import os
import pathlib
import signal
import subprocess
import time
import urllib.error
import urllib.request

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def wait_for_output_line(proc: subprocess.Popen[str], text: str, timeout: float = 90) -> str:
    deadline = time.time() + timeout
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


def assert_connection_state(url: str, expect_up: bool) -> None:
    try:
        with urllib.request.urlopen(url, timeout=1):
            pass
    except urllib.error.URLError:
        assert not expect_up
        return
    assert expect_up


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
        assert_connection_state(f"http://{container_ip}:8000", True)
        assert_connection_state("http://127.0.0.1:8000", False)
        assert_no_jjx_in_charm_venv(k8s_2_configurable)
        # Teardown
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=5) == 130
        assert_connection_state(f"http://{container_ip}:8000", False)
    finally:
        if proc.poll() is None:
            proc.kill()


# TODO: test uvx jjx again, this time setting a port mapping with -p


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


# TODO: manually set up a running workload container then test that `jjx down` tears it down


# TODO: break the integration test and check that jjx exits immediately
