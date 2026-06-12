import os
import pathlib
import signal
import subprocess
import time

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def assert_output_contains(proc: subprocess.Popen[str], text: str, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        if text in line:
            return
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
        assert_output_contains(proc, "Workload container is running")
        # TODO: assert that we can hit the server's API
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130
        # TODO: assert that we can't hit the server's API
        assert_no_jjx_in_charm_venv(k8s_2_configurable)
    finally:
        if proc.poll() is None:
            proc.kill()


# TODO: test uvx jjx again, this time setting a port mapping with -p


# TODO: test what happens if we install jjx in the charm's venv and 'uv run' the CLI


# TODO: manually set up a running workload container then test that `jjx destroy` tears it down
