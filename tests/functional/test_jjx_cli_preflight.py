import os
import pathlib
import subprocess

import pytest

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
    assert "Error: pytest-jubilant is not in the 'integration' dependency group." in result.stderr
    assert not (k8s_2_configurable / ".jjx").exists()
    assert not (k8s_2_configurable / "placeholder.charm").exists()


@pytest.mark.docker
def test_jjx_cli_needs_docker(k8s_2_configurable):
    # Put a failing docker shim first on PATH so jjx's preflight check fails.
    # Pin JJX_RUNTIME=docker so jjx actually uses the shim: detection prefers
    # Podman when it is installed, which would dodge the shim on this host.
    docker_shim_dir = SHIMS_DIR / "docker-unavailable"
    env = {
        **os.environ,
        "PATH": f"{docker_shim_dir}:{os.environ['PATH']}",
        "JJX_RUNTIME": "docker",
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
    assert "Error: docker is not running." in result.stderr
    assert not (k8s_2_configurable / ".jjx").exists()
    assert not (k8s_2_configurable / "placeholder.charm").exists()


@pytest.mark.podman
def test_jjx_cli_needs_podman(k8s_2_configurable):
    # Put a failing podman shim first on PATH so jjx's preflight check fails.
    # Pin JJX_RUNTIME=podman so jjx actually uses the shim: detection falls
    # back to Docker when Podman is not on PATH, which would dodge the shim.
    podman_shim_dir = SHIMS_DIR / "podman-unavailable"
    env = {
        **os.environ,
        "PATH": f"{podman_shim_dir}:{os.environ['PATH']}",
        "JJX_RUNTIME": "podman",
    }
    # Run jjx. It should fail fast because Podman is not available.
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
    assert "Error: podman is not running." in result.stderr
    assert not (k8s_2_configurable / ".jjx").exists()
    assert not (k8s_2_configurable / "placeholder.charm").exists()
