"""Patches the stored k8s-2-configurable charm with a get_version() retry loop."""

from pathlib import Path

CHARM_PY = Path("tests/functional/charms/k8s-2-configurable/src/charm.py")

content = CHARM_PY.read_text()
content = content.replace(
    "import logging\n",
    "import logging\nimport time\nimport urllib.error\n",
)
content = content.replace(
    "        version = fastapi_demo.get_version(config.server_port)\n",
    "        for attempt in range(3):\n"
    "            if attempt:\n"
    "                time.sleep(1)\n"
    "            try:\n"
    "                version = fastapi_demo.get_version(config.server_port)\n"
    "                break\n"
    "            except urllib.error.URLError:\n"
    '                logger.info("Workload not yet available (attempt %d)", attempt + 1)\n'
    "        else:\n"
    '            logger.error("The workload was not available within the expected time")\n'
    '            raise RuntimeError("workload is not available")\n',
)
CHARM_PY.write_text(content)
print(f"Patched {CHARM_PY}")
