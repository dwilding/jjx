"""Virtual prometheus-k8s provider.

This module implements a minimal "virtual charm" for prometheus-k8s that runs
a real Prometheus instance in a Docker container and reads the scrape job
configuration from the charm's relation databag (written by
``MetricsEndpointProvider``).

The charm's ``MetricsEndpointProvider`` writes:
- ``scrape_jobs``: JSON array of scrape job configs (to the charm's app databag)
- ``scrape_metadata``: JSON with topology info (to the charm's app databag)
- ``prometheus_scrape_unit_address``: the workload's IP (to the charm's unit databag)
- ``prometheus_scrape_unit_name``: the unit name (to the charm's unit databag)

The virtual Prometheus reads these and generates a ``prometheus.yml`` that
scrapes the workload's ``/metrics`` endpoint at its bridge IP.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import _engine

PROMETHEUS_IMAGE = "docker.io/prom/prometheus:v3.5.0"
PROMETHEUS_PORT = 9090


def _wait_for_prometheus(container_name: str, timeout: float = 60.0) -> None:
    """Wait until Prometheus is ready to serve queries."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            details = _engine._docker_container_details(container_name)
            if not details.running or not details.ip_address:
                time.sleep(1.0)
                continue
            url = f"http://{details.ip_address}:{PROMETHEUS_PORT}/-/ready"
            with urllib.request.urlopen(url, timeout=5.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, _engine.CliError):
            pass
        time.sleep(1.0)
    raise _engine.CliError(f"prometheus did not become ready in {container_name}")


def start_prometheus(
    model_name: str,
    app_name: str,
) -> dict[str, Any]:
    """Start a Prometheus container and return provider state.

    Returns a dict with keys: container_name, container_id, ip_address, host, port.
    """
    container_name = _engine._sanitize_container_name(f"{model_name}-{app_name}")

    # Remove any stale container with the same name.
    _engine._docker_rm(container_name)

    # Start Prometheus with an empty default config. The real config is
    # written after integrate, when we know the workload's scrape target.
    # We use a tmpfs for /prometheus (TSDB data) so it doesn't persist.
    #
    # The config directory must be under the project's .jjx/ dir (not /tmp)
    # because /tmp may have Docker bind mount propagation issues.
    jjx_dir = _engine._jjx_dir()
    config_dir = jjx_dir / f"prom-config-{app_name}"
    config_dir.mkdir(parents=True, exist_ok=True)
    _write_prometheus_config(config_dir, scrape_configs=[])
    # The Prometheus image runs as user 'nobody' (UID 65534), so the config
    # directory and file must be world-readable.
    config_dir.chmod(0o755)
    (config_dir / "prometheus.yml").chmod(0o644)

    container_id = _engine._docker_run(
        PROMETHEUS_IMAGE,
        container_name,
        mounts=[(str(config_dir), "/etc/prometheus", False)],
        tmpfs_mounts=["/prometheus:mode=1777"],
        command=[
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
            "--web.enable-lifecycle",
        ],
    )

    _wait_for_prometheus(container_name)

    details = _engine._docker_container_details(container_name)
    if not details.running:
        raise _engine.CliError(f"prometheus container {container_name} is not running")

    return {
        "container_name": container_name,
        "container_id": container_id,
        "ip_address": details.ip_address,
        "host": details.ip_address,
        "port": PROMETHEUS_PORT,
        "config_dir": str(config_dir),
    }


def _write_prometheus_config(config_dir: Path, scrape_configs: list[dict[str, Any]]) -> None:
    """Write a prometheus.yml config file."""
    config = {
        "global": {
            "scrape_interval": "5s",
        },
        "scrape_configs": scrape_configs,
    }

    (config_dir / "prometheus.yml").write_text(
        yaml.safe_dump(config, default_flow_style=False),
        encoding="utf-8",
    )
    (config_dir / "prometheus.yml").chmod(0o644)


def _reload_prometheus(container_name: str) -> None:
    """Reload Prometheus config via the HTTP API."""
    details = _engine._docker_container_details(container_name)
    if not details.running or not details.ip_address:
        return
    url = f"http://{details.ip_address}:{PROMETHEUS_PORT}/-/reload"
    try:
        req = urllib.request.Request(url, method="POST")
        urllib.request.urlopen(req, timeout=5.0)
    except (urllib.error.URLError, urllib.error.HTTPError):
        # If reload fails, the config will be picked up on next restart
        pass


def populate_relation(
    model_state: dict[str, Any],
    relation: dict[str, Any],
    provider_app: str,
    prom_info: dict[str, Any],
) -> None:
    """Read the charm's scrape config from the relation and configure Prometheus.

    The charm's ``MetricsEndpointProvider`` writes ``scrape_jobs`` and
    ``scrape_metadata`` to its app databag, and ``prometheus_scrape_unit_address``
    to its unit databag. We read these, resolve wildcard targets to the
    workload's IP, and write a ``prometheus.yml`` that scrapes the workload.
    """
    # The charm is the provider (provides metrics-endpoint), Prometheus is the
    # consumer (requires metrics-endpoint). So the charm's data is in the
    # remote app's databag.
    # Find the charm's app name (the non-virtual side of the relation).
    charm_app = None
    for app_name in relation.get("endpoints", {}):
        if app_name != provider_app:
            charm_app = app_name
            break
    if charm_app is None:
        return

    data = relation.get("data", {})
    charm_data = data.get(charm_app, {})
    charm_app_data = charm_data.get("app", {})
    charm_unit_data = charm_data.get(f"{charm_app}/0", {})

    scrape_jobs_json = charm_app_data.get("scrape_jobs", "[]")
    scrape_jobs = json.loads(scrape_jobs_json) if scrape_jobs_json else []

    # Get the workload's address from the charm's unit databag
    unit_address = charm_unit_data.get("prometheus_scrape_unit_address", "")

    if not scrape_jobs:
        return

    # Resolve wildcard targets (*:port) to the workload's IP
    resolved_configs = []
    for job in scrape_jobs:
        job = dict(job)
        static_configs = job.get("static_configs", [])
        new_static_configs = []
        for sc in static_configs:
            sc = dict(sc)
            targets = sc.get("targets", [])
            resolved_targets = []
            for target in targets:
                if target.startswith("*:"):
                    port = target[2:]
                    if unit_address:
                        resolved_targets.append(f"{unit_address}:{port}")
                    else:
                        resolved_targets.append(f"localhost:{port}")
                else:
                    resolved_targets.append(target)
            sc["targets"] = resolved_targets
            new_static_configs.append(sc)
        job["static_configs"] = new_static_configs
        # Use a stable job name based on the charm app
        job.setdefault("job_name", f"juju_{charm_app}")
        resolved_configs.append(job)

    # Write the config and reload
    config_dir = Path(prom_info["config_dir"])
    _write_prometheus_config(config_dir, resolved_configs)
    _reload_prometheus(prom_info["container_name"])
