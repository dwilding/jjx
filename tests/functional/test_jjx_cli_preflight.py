import os
import pathlib
import subprocess

PACKAGE_DIR = pathlib.Path(__file__).parent.parent.parent
SHIMS_DIR = pathlib.Path(__file__).parent / "shims"


def test_jjx_cli_needs_plugin(k8s_2_configurable):
    # Remove pytest-jubilant so jjx's preflight check fails fast.
    command = [
        "uv",
        "remove",
        "--group",
        "integration",
        "pytest-jubilant",
    ]
    subprocess.run(
        command,
        cwd=k8s_2_configurable,
        check=True,
    )
    # Run jjx. It should fail fast because pytest-jubilant is missing.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
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
    assert "ERROR: pytest-jubilant is not in the 'integration' dependency group." in result.stderr
    assert not (k8s_2_configurable / ".jjx").exists()
    assert not (k8s_2_configurable / "placeholder.charm").exists()


def test_jjx_cli_needs_docker(k8s_2_configurable):
    # Put a failing docker shim first on PATH so jjx's preflight check fails.
    docker_shim_dir = SHIMS_DIR / "docker-unavailable"
    env = {
        **os.environ,
        "PATH": f"{docker_shim_dir}:{os.environ['PATH']}",
    }
    # Run jjx. It should fail fast because Docker is not available.
    command = [
        "uvx",
        "--with-editable",
        PACKAGE_DIR,
        "jjx",
    ]
    result = subprocess.run(
        command,
        cwd=k8s_2_configurable,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "ERROR: Docker is not running." in result.stderr
    assert not (k8s_2_configurable / ".jjx").exists()
    assert not (k8s_2_configurable / "placeholder.charm").exists()
