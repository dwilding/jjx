import os
import pathlib
import signal
import subprocess
import time

import helpers_functional as helpers

import jjx

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent


def test_jjx_down_removes_orphaned_layer_copy_containers(temp_dir):
    # Create a fake orphaned jjx-layer-copy-* container.
    # A Ctrl-C mid-deploy would leave one behind.
    runtime = jjx.container_runtime()
    orphan_name = "jjx-layer-copy-fake-orphan"
    command = [
        runtime,
        "create",
        "--name",
        orphan_name,
        "hello-world",
        "true",
    ]
    subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )
    helpers.assert_container(orphan_name)
    # Run jjx down from a dir with no state. It should still sweep orphans.
    work_dir = temp_dir / "layer_copy_down"
    work_dir.mkdir()
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
        "down",
    ]
    result = subprocess.run(
        command,
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"jjx down exited with code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert f"Removed {orphan_name}" in result.stdout
    helpers.assert_no_container(orphan_name)


def test_ctrl_c_tears_down(k8s_2_configurable):
    # Run jjx in its own process group so SIGINT reaches the whole group.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
    ]
    proc = subprocess.Popen(
        command,
        cwd=k8s_2_configurable,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    # Wait for deploy to start. The first container appearing means deploy is in flight.
    runtime = jjx.container_runtime()
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        command = [
            runtime,
            "ps",
            "--all",
            "--format",
            "{{.Names}}",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if any(name.strip() for name in result.stdout.splitlines()):
            break
        if proc.poll() is not None:
            raise AssertionError("jjx exited before deploy started")
        time.sleep(0.2)
    else:
        proc.terminate()
        proc.communicate()
        raise AssertionError("jjx did not start any container within 180s")
    # Send Ctrl-C. SIGINT goes to the whole process group.
    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    try:
        proc.communicate(timeout=60.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise AssertionError("jjx did not exit within 60s of SIGINT")
    assert proc.returncode == 130
    # Check that no containers leaked.
    command = [
        runtime,
        "ps",
        "--all",
        "--format",
        "{{.Names}}",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    leftover = [n for n in result.stdout.splitlines() if n.strip()]
    assert not leftover
    assert not (k8s_2_configurable / ".jjx").exists()
