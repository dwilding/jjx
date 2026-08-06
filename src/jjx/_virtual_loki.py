"""Virtual loki-k8s provider.

This module implements a minimal "virtual charm" for loki-k8s that runs a real
Loki instance in a Docker container and writes the relation data that a
``LokiPushApiProvider`` charm would write, so that charms using ``LogForwarder``
from the loki_push_api library can integrate with it.

This is not a full charm — it has no charm code, no Pebble, no hooks. It
directly manages the relation databag in jjx state, mimicking the output of
the loki-k8s charm's provider side.

The key relation data field is ``endpoint`` in the provider's **unit** databag,
which ``LogForwarder._extract_urls`` reads:

    relation.data[unit]["endpoint"] = json.dumps({"url": "http://<ip>:3100/loki/api/v1/push"})

When the charm's ``LogForwarder`` receives ``relation-changed``, it reads this
URL and configures a Pebble ``log-target`` of type ``loki`` pointing at it.
Since jjx runs real Pebble, the logs actually flow — no simulation.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from . import _engine


LOKI_IMAGE = "docker.io/grafana/loki:3.5.5"
LOKI_PORT = 3100


def _wait_for_loki(container_name: str, timeout: float = 60.0) -> None:
    """Wait until Loki is ready to accept push requests.

    Loki exposes a ``/ready`` endpoint that returns HTTP 200 when the instance
    is ready to receive traffic. We check from the host using the container's
    bridge IP, avoiding any dependency on tools inside the Loki image.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            details = _engine._docker_container_details(container_name)
            if not details.running or not details.ip_address:
                time.sleep(1.0)
                continue
            url = f"http://{details.ip_address}:{LOKI_PORT}/ready"
            with urllib.request.urlopen(url, timeout=5.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, _engine.CliError):
            pass
        time.sleep(1.0)
    raise _engine.CliError(f"loki did not become ready in {container_name}")


def start_loki(
    model_name: str,
    app_name: str,
) -> dict[str, Any]:
    """Start a Loki container and return provider state.

    Returns a dict with keys: container_name, container_id, ip_address, host, port.

    The Loki Docker image ships with a built-in default config at
    ``/etc/loki/local-config.yaml`` that runs Loki in single-binary mode with
    filesystem storage. This is sufficient for integration testing — no custom
    config file is needed.
    """
    container_name = _engine._sanitize_container_name(f"{model_name}-{app_name}")

    # Remove any stale container with the same name.
    _engine._docker_rm(container_name)

    container_id = _engine._docker_run(
        LOKI_IMAGE,
        container_name,
        # The image's default command is:
        #   /usr/bin/loki -config.file=/etc/loki/local-config.yaml
        # which starts Loki in single-binary mode with filesystem storage.
        # No custom config or command is needed.
    )

    _wait_for_loki(container_name)

    details = _engine._docker_container_details(container_name)
    if not details.running:
        raise _engine.CliError(f"loki container {container_name} is not running")

    return {
        "container_name": container_name,
        "container_id": container_id,
        "ip_address": details.ip_address,
        "host": details.ip_address,
        "port": LOKI_PORT,
    }


def populate_relation(
    model_state: dict[str, Any],
    relation: dict[str, Any],
    provider_app: str,
    loki_info: dict[str, Any],
) -> None:
    """Write the provider-side relation data.

    This mimics what the loki-k8s charm's LokiPushApiProvider would write:
    - ``endpoint``: JSON object with a ``url`` key, in the provider's **unit**
      databag. This is what ``LogForwarder._extract_urls`` reads.
    - ``public_address``: the Loki unit's address (informational).

    The ``LogForwarder`` library reads ``relation.data[unit]["endpoint"]``,
    deserializes the JSON, and extracts the ``url`` field. It then configures
    a Pebble ``log-target`` of type ``loki`` pointing at that URL.
    """
    from . import _virtual_registry

    # Refresh the container IP and build the push URL.
    base_url = _virtual_registry.resolve_endpoint_url(loki_info, LOKI_PORT)
    url = f"{base_url}/loki/api/v1/push"

    # Write the endpoint to the provider's unit databag.
    # LogForwarder._extract_urls iterates relation.units and reads
    # relation.data[unit]["endpoint"].
    unit_name = f"{provider_app}/0"
    unit_bucket = _engine._relation_data_bucket(relation, provider_app, unit_name)
    unit_bucket["endpoint"] = json.dumps({"url": url})
    unit_bucket["public_address"] = loki_info.get("host", "")
