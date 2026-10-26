"""Bumps dependency versions to the latest release published more than 7 days ago."""

import json
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

CUTOFF = datetime.now(UTC) - timedelta(days=7)


def requirements(dev: bool) -> list[str]:
    """Return the project's requirements, as reported by uv tree."""
    cmd = ["uv", "tree", "--depth", "1", "--frozen", "--only-dev" if dev else "--no-dev"]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return re.findall(r"[├└]── (\S+) v(\S+)", result.stdout)


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
    versions = []
    for version, files in data["releases"].items():
        if not files:
            continue
        parsed = parse_version(version)
        if parsed is None:
            continue  # Prerelease or non-standard version.
        upload_time = max(f["upload_time_iso_8601"] for f in files)
        versions.append((parsed, version, upload_time))
    for parsed, version, upload_time in sorted(versions, reverse=True):
        if datetime.fromisoformat(upload_time) <= CUTOFF:
            return version
    sys.exit(f"Error: no stable release of {package} older than 7 days")


def bump_build(uv_version: str) -> None:
    content = Path("pyproject.toml").read_text()
    content = re.sub(r'"uv_build==[^"]+"', f'"uv_build=={uv_version}"', content)
    Path("pyproject.toml").write_text(content)
    subprocess.run(["uv", "lock"], check=True)


def bump_precommit(just_version: str) -> None:
    path = Path(".pre-commit-config.yaml")
    path.write_text(
        re.sub(r"rust-just@[0-9][0-9.]*", f"rust-just@{just_version}", path.read_text())
    )


def bump_workflows(just_version: str, borescope_version: str) -> None:
    for path in Path(".github/workflows").glob("*.yaml"):
        path.write_text(
            re.sub(r"rust-just@[0-9][0-9.]*", f"rust-just@{just_version}", path.read_text())
        )
    borescope_path = Path(".github/workflows/borescope.yaml")
    borescope_path.write_text(
        re.sub(
            r"borescope@[0-9][0-9.]*", f"borescope@{borescope_version}", borescope_path.read_text()
        )
    )


def main() -> None:
    for dev in (False, True):
        for requirement, current_version in requirements(dev):
            package = requirement.split("[")[0]
            version = latest_version(package)
            if version == current_version:
                print(f"{package}: {version} (up to date)")
                continue
            print(f"{package}: {current_version} -> {version}")
            cmd = ["uv", "add"]
            if dev:
                cmd.append("--dev")
            cmd.append(f"{requirement}=={version}")
            subprocess.run(cmd, check=True)
    uv_version = latest_version("uv_build")
    print(f"uv_build: {uv_version}")
    bump_build(uv_version)
    just_version = latest_version("rust-just")
    print(f"rust-just: {just_version}")
    bump_precommit(just_version)
    borescope_version = latest_version("borescope")
    print(f"borescope: {borescope_version}")
    bump_workflows(just_version, borescope_version)


if __name__ == "__main__":
    main()
