#!/usr/bin/env python3
"""Bump dependency versions to the latest release published more than 7 days ago."""

import json
import re
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# (PyPI name, requirement spec with {version} placeholder)
RUNTIME = [
    ("pyyaml", "pyyaml=={version}"),
    ("tomli", "tomli=={version} ; python_full_version < '3.11'"),
]
DEV = [
    ("ruff", "ruff=={version}"),
    ("pytest", "pytest=={version}"),
    ("ty", "ty=={version}"),
    ("zizmor", "zizmor=={version}"),
]
# Packages bumped by editing files directly (no spec template needed).
BUILD = ["uv_build"]
WORKFLOWS = ["rust-just"]

CUTOFF = datetime.now(timezone.utc) - timedelta(days=7)


def parse_version(version: str) -> tuple[int, ...] | None:
    """Parse a simple numeric version string like '2.4.1' into a comparable tuple.

    Returns None for anything that isn't purely numeric (prereleases, post-releases,
    local versions, etc.), which doubles as a stable-release filter.
    """
    parts = version.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def latest_version(package: str) -> str:
    """Return the latest non-prerelease version of *package* published > 7 days ago."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json") as resp:
        data = json.load(resp)
    best_version = None
    for version, files in data["releases"].items():
        if not files:
            continue
        parsed = parse_version(version)
        if parsed is None:
            continue  # prerelease or non-standard version
        upload_time = max(f["upload_time_iso_8601"] for f in files)
        dt = datetime.fromisoformat(upload_time.replace("Z", "+00:00"))
        if dt > CUTOFF:
            continue
        if best_version is None or parsed > best_version[0]:
            best_version = (parsed, version)
    assert best_version is not None, f"no stable release of {package} older than 7 days"
    return best_version[1]


def uv_add(spec: str, dev: bool) -> None:
    cmd = ["uv", "add"]
    if dev:
        cmd.append("--dev")
    cmd.append(spec)
    subprocess.run(cmd, check=True)


def bump_build(package: str, version: str) -> None:
    path = Path("pyproject.toml")
    content = path.read_text()
    content = re.sub(
        rf'"{re.escape(package)}==[^"]+"',
        f'"{package}=={version}"',
        content,
    )
    path.write_text(content)


def bump_workflows(package: str, version: str) -> None:
    for path in sorted(Path(".github/workflows").glob("*.yaml")):
        content = path.read_text()
        new = re.sub(
            rf"{re.escape(package)}@[0-9][0-9.]*",
            f"{package}@{version}",
            content,
        )
        if new != content:
            path.write_text(new)


def main() -> None:
    for package, spec in RUNTIME:
        version = latest_version(package)
        print(f"{package}: {version}")
        uv_add(spec.format(version=version), dev=False)

    for package, spec in DEV:
        version = latest_version(package)
        print(f"{package}: {version}")
        uv_add(spec.format(version=version), dev=True)

    for package in BUILD:
        version = latest_version(package)
        print(f"{package}: {version}")
        bump_build(package, version)

    for package in WORKFLOWS:
        version = latest_version(package)
        print(f"{package}: {version}")
        bump_workflows(package, version)

    # Re-lock to pick up the build-system requirement change.
    subprocess.run(["uv", "lock"], check=True)


if __name__ == "__main__":
    main()
