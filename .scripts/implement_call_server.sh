#!/bin/bash
set -euo pipefail

cd tests/functional/charms/k8s-2-configurable-call-server/tests/integration

# Replace the 'import logging' line.
sed -i 's/^import logging$/import json\nimport logging\nimport urllib.request/' test_charm.py

# Replace the version lookup.
sed -i '/version = juju.status().apps\["fastapi-demo"\].version/c\
    unit_ip = juju.status().apps[APP_NAME].units[f"{APP_NAME}/0"].address\
    response = urllib.request.urlopen(f"http://{unit_ip}:8000/version")\
    data = json.loads(response.read())\
    version = data["version"]' test_charm.py

tox -e format,lint
