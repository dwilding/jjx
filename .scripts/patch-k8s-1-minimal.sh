#!/bin/bash
set -euo pipefail

cd tests/functional/charms/k8s-1-minimal-patched/tests/integration

# Replace the 'import logging' line.
sed -i 's/^import logging$/import json\nimport logging\nimport urllib.request/' test_charm.py

# Append a test that talks directly to the application.
# Reuse the version already declared earlier in the file.
existing_version=$(awk -F'"' '/expected_version = /{print $2; exit}' test_charm.py)
cat >> test_charm.py <<PYEOF


def test_workload_version_direct(charm: pathlib.Path, juju: jubilant.Juju):
    """Verify that integration tests can talk directly to the application."""
    expected_version = "$existing_version"
    unit_ip = juju.status().apps[APP_NAME].units[f"{APP_NAME}/0"].address
    response = urllib.request.urlopen(f"http://{unit_ip}:8000/version")
    data = json.loads(response.read())
    assert data["version"] == expected_version
PYEOF

UV_NO_CONFIG=1 tox -e format,lint  # Run with UV_NO_CONFIG to ignore repo's exclude-newer config.
