"""Shared helpers for functional tests.

These are plain functions (not fixtures) used by test modules. Fixtures live
in conftest.py. This mirrors the gimmegit test layout.
"""

from __future__ import annotations

import subprocess

import jjx


def assert_container(container_name: str) -> None:
    """Assert that a container with the given name exists."""
    result = subprocess.run(
        [jjx.container_runtime(), "inspect", container_name],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"container {container_name} does not exist or inspect failed: {result.stderr.strip()}"
    )


def assert_no_container(container_name: str) -> None:
    """Assert that no container with the given name exists."""
    result = subprocess.run(
        [jjx.container_runtime(), "inspect", container_name],
        capture_output=True,
        text=True,
    )
    # docker returns 1 for a missing container; podman returns 125. Use != 0
    # so this works regardless of runtime.
    assert result.returncode != 0, f"container {container_name} still exists"


def container_ip(container_name: str) -> str:
    """Return the IP address of a container via inspect."""
    result = subprocess.run(
        [
            jjx.container_runtime(),
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container_name,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"inspect exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip()
