"""Sets JUJU_VERSION and PEBBLE_VERSION to match the latest Juju 4 release.

JUJU_VERSION comes from the Snap Store's 4/stable channel, because GitHub's
"latest" release is sometimes an old 3.6.x. PEBBLE_VERSION comes from the
github.com/canonical/pebble pin in Juju's go.mod at the release tag, because
Juju builds the Pebble binary it injects into containers from that pin.
Pebble moves only when Juju moves.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

VERSION_FILE = Path("src/jjx/_version.py")


def latest_juju() -> str:
    url = "https://api.snapcraft.io/v2/snaps/info/juju?fields=channel-map,version"
    request = urllib.request.Request(url, headers={"Snap-Device-Series": "16"})
    with urllib.request.urlopen(request) as resp:
        data = json.load(resp)
    for entry in data["channel-map"]:
        if entry["channel"]["name"] == "4/stable" and entry["channel"]["architecture"] == "amd64":
            return entry["version"]
    sys.exit("Error: no 4/stable channel found for Juju in the Snap Store")


def latest_juju_pebble(juju_version: str) -> str:
    url = f"https://raw.githubusercontent.com/juju/juju/v{juju_version}/go.mod"
    with urllib.request.urlopen(url) as resp:
        for line in resp:
            parts = line.decode().split()
            if parts and parts[0] == "github.com/canonical/pebble":
                return parts[1]
    sys.exit(f"Error: no github.com/canonical/pebble pin in Juju {juju_version} go.mod")


def current_juju() -> str:
    content = VERSION_FILE.read_text()
    juju = re.search(r'^JUJU_VERSION = "(.+)"$', content, re.MULTILINE)
    if juju is None:
        sys.exit(f"Error: could not read JUJU_VERSION from {VERSION_FILE}")
    return juju.group(1)


def set_var(content: str, name: str, version: str) -> str:
    return re.sub(rf"^{name} = .+$", f'{name} = "{version}"', content, flags=re.MULTILINE)


def main() -> None:
    juju_version = latest_juju()
    if not re.fullmatch(r"4\.\d+\.\d+", juju_version):
        sys.exit(f"Error: Juju 4/stable version {juju_version!r} is not 4.x")
    pebble_version = latest_juju_pebble(juju_version)
    if not re.fullmatch(r"v\d+\.\d+\.\d+", pebble_version):
        sys.exit(
            f"Error: Pebble pin {pebble_version!r} for Juju {juju_version} is not a release tag"
        )
    current = current_juju()
    if current == juju_version:
        print(f"Current Juju version is already {juju_version}")
        return
    content = VERSION_FILE.read_text()
    content = set_var(content, "JUJU_VERSION", juju_version)
    content = set_var(content, "PEBBLE_VERSION", pebble_version)
    VERSION_FILE.write_text(content)
    print(f"Juju {current} -> {juju_version} (Pebble {pebble_version})")


if __name__ == "__main__":
    main()
