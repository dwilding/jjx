"""Sets a new x.y.z package version."""

import re
import subprocess
import sys
from pathlib import Path

VERSION_FILE = Path("src/jjx/_version.py")


def parse_version(version: str) -> tuple[int, ...] | None:
    """Parse 'x.y.z' or 'x.y.z.devN' into a comparable tuple.

    Returns None for anything else. A dev version sorts just before the
    corresponding final release.
    """
    match = re.fullmatch(r"(\d+(?:\.\d+)*)\.dev\d+", version)
    if match:
        return tuple(int(part) for part in match.group(1).split(".")) + (-1,)
    parts = version.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts) + (0,)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: bump_version.py VERSION")
    new = sys.argv[1]
    new_parsed = parse_version(new)
    if new_parsed is None or len(new_parsed) != 4 or new_parsed[-1] != 0:
        sys.exit(f"Error: {new} is not an x.y.z version.")
    current = subprocess.run(
        ["uv", "version", "--short"], capture_output=True, text=True, check=True
    ).stdout.strip()
    current_parsed = parse_version(current)
    assert current_parsed is not None
    if new_parsed <= current_parsed:
        sys.exit(f"Error: {new} does not exceed the current version {current}.")
    subprocess.run(["uv", "version", new, "--no-sync"], check=True)
    VERSION_FILE.write_text(
        re.sub(r'(?m)^__version__ = ".*"$', f'__version__ = "{new}"', VERSION_FILE.read_text())
    )


if __name__ == "__main__":
    main()
