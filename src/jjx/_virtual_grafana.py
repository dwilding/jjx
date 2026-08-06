"""Virtual grafana-k8s provider.

This module implements a minimal "virtual charm" for grafana-k8s that runs
a real Grafana instance in a Docker container, provisioned with Prometheus
and Loki as datasources, and imports dashboards from the charm's relation
databag (written by ``GrafanaDashboardProvider``).

The charm's ``GrafanaDashboardProvider`` writes compressed dashboard JSON to
the ``dashboards`` key in its app databag. The dashboards are LZMA-compressed
and base64-encoded. The virtual Grafana decodes them using stdlib ``lzma``
and ``base64`` and imports them via Grafana's provisioning system.
"""

from __future__ import annotations

import base64
import json
import lzma
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import _engine


GRAFANA_IMAGE = "docker.io/grafana/grafana:12.1.0"
GRAFANA_PORT = 3000


def _wait_for_grafana(container_name: str, timeout: float = 60.0) -> None:
    """Wait until Grafana is ready to serve requests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            details = _engine._docker_container_details(container_name)
            if not details.running or not details.ip_address:
                time.sleep(1.0)
                continue
            url = f"http://{details.ip_address}:{GRAFANA_PORT}/api/health"
            with urllib.request.urlopen(url, timeout=5.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, urllib.error.HTTPError, _engine.CliError):
            pass
        time.sleep(1.0)
    raise _engine.CliError(f"grafana did not become ready in {container_name}")


def start_grafana(
    model_name: str,
    app_name: str,
) -> dict[str, Any]:
    """Start a Grafana container and return provider state.

    Returns a dict with keys: container_name, container_id, ip_address, host, port.

    Grafana is provisioned with datasources and dashboards via files mounted
    into ``/etc/grafana/provisioning/``. The datasource and dashboard configs
    are written after integrate, when we know the Prometheus and Loki URLs.
    """
    container_name = _engine._sanitize_container_name(f"{model_name}-{app_name}")

    # Remove any stale container with the same name.
    _engine._docker_rm(container_name)

    # The provisioning directory must be under the project's .jjx/ dir (not
    # /tmp) because /tmp may have Docker bind mount propagation issues.
    jjx_dir = _engine._jjx_dir()
    provisioning_dir = jjx_dir / f"grafana-config-{app_name}"
    ds_dir = provisioning_dir / "datasources"
    dash_dir = provisioning_dir / "dashboards"
    ds_dir.mkdir(parents=True, exist_ok=True)
    dash_dir.mkdir(parents=True, exist_ok=True)
    # The Grafana image runs as UID 472, so provisioning dirs must be
    # world-readable/writable.
    for d in [provisioning_dir, ds_dir, dash_dir]:
        d.chmod(0o777)

    # Write empty provisioning files for now — they'll be updated after integrate
    _write_datasource_config(ds_dir, prometheus_url="", loki_url="")
    _write_dashboard_provider_config(dash_dir)

    container_id = _engine._docker_run(
        GRAFANA_IMAGE,
        container_name,
        mounts=[(str(provisioning_dir), "/etc/grafana/provisioning", False)],
        env={
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD": "admin",
            "GF_AUTH_ANONYMOUS_ENABLED": "true",
            "GF_AUTH_ANONYMOUS_ORG_ROLE": "Admin",
        },
    )

    _wait_for_grafana(container_name)

    details = _engine._docker_container_details(container_name)
    if not details.running:
        raise _engine.CliError(f"grafana container {container_name} is not running")

    return {
        "container_name": container_name,
        "container_id": container_id,
        "ip_address": details.ip_address,
        "host": details.ip_address,
        "port": GRAFANA_PORT,
        "provisioning_dir": str(provisioning_dir),
    }


def _write_datasource_config(ds_dir: Path, prometheus_url: str, loki_url: str) -> None:
    """Write Grafana datasource provisioning config."""
    datasources = {"apiVersion": 1, "datasources": []}
    if prometheus_url:
        datasources["datasources"].append(
            {
                "name": "Prometheus",
                "type": "prometheus",
                "uid": "prometheusds",
                "url": prometheus_url,
                "access": "proxy",
                "isDefault": True,
            }
        )
    if loki_url:
        datasources["datasources"].append(
            {
                "name": "Loki",
                "type": "loki",
                "uid": "lokids",
                "url": loki_url,
                "access": "proxy",
            }
        )

    (ds_dir / "datasources.yaml").write_text(
        yaml.safe_dump(datasources, default_flow_style=False),
        encoding="utf-8",
    )
    (ds_dir / "datasources.yaml").chmod(0o666)


def _write_dashboard_provider_config(dash_dir: Path) -> None:
    """Write Grafana dashboard provider config."""
    config = {
        "apiVersion": 1,
        "providers": [
            {
                "name": "jjx",
                "orgId": 1,
                "folder": "",
                "type": "file",
                "disableDeletion": False,
                "editable": True,
                "updateIntervalSeconds": 5,
                "options": {"path": "/etc/grafana/provisioning/dashboards"},
            }
        ],
    }

    (dash_dir / "dashboards.yaml").write_text(
        yaml.safe_dump(config, default_flow_style=False),
        encoding="utf-8",
    )
    (dash_dir / "dashboards.yaml").chmod(0o666)


def update_datasources(
    grafana_info: dict[str, Any],
    prometheus_url: str,
    loki_url: str,
) -> None:
    """Update Grafana's datasource provisioning and restart to pick up changes."""
    provisioning_dir = Path(grafana_info["provisioning_dir"])
    ds_dir = provisioning_dir / "datasources"
    _write_datasource_config(ds_dir, prometheus_url, loki_url)
    # Restart Grafana to pick up the new datasource config. Grafana does not
    # re-provision datasources on SIGHUP, so a full container restart is needed.
    _engine._docker_restart(grafana_info["container_name"])
    _wait_for_grafana(grafana_info["container_name"], timeout=30.0)


def import_dashboards(
    grafana_info: dict[str, Any],
    relation: dict[str, Any],
    charm_app: str,
) -> None:
    """Import dashboards from the charm's relation databag into Grafana.

    The charm's ``GrafanaDashboardProvider`` writes dashboard JSON to the
    ``dashboards`` key in its app databag. The dashboards are LZMA-compressed
    and base64-encoded. We decode them and write them as JSON files in
    Grafana's dashboard provisioning directory.
    """
    data = relation.get("data", {})
    charm_data = data.get(charm_app, {})
    charm_app_data = charm_data.get("app", {})

    dashboards_json = charm_app_data.get("dashboards", "")
    if not dashboards_json:
        return

    dashboards_data = json.loads(dashboards_json)
    templates = dashboards_data.get("templates", {})

    provisioning_dir = Path(grafana_info["provisioning_dir"])
    dash_dir = provisioning_dir / "dashboards"

    # Clear old dashboard files
    for f in dash_dir.glob("*.json"):
        f.unlink()

    for key, template in templates.items():
        content = template.get("content", "")
        if not content:
            continue
        try:
            decoded = lzma.decompress(base64.b64decode(content)).decode("utf-8")
            dashboard = json.loads(decoded)
            _inject_datasource_variables(dashboard)
            safe_name = key.replace(":", "_").replace("/", "_")
            dash_path = dash_dir / f"{safe_name}.json"
            dash_path.write_text(json.dumps(dashboard), encoding="utf-8")
            dash_path.chmod(0o666)
        except Exception:
            pass


def _inject_datasource_variables(dashboard: dict[str, Any]) -> None:
    """Inject datasource variables for ${prometheusds} and ${lokids} references.

    Dashboards from charms reference datasources via ``${prometheusds}`` and
    ``${lokids}`` UID placeholders. The real grafana-k8s charm injects matching
    datasource variables into the dashboard's templating list when it imports
    the dashboard, so Grafana can resolve the placeholders to the provisioned
    datasources. We do the same here.
    """
    # The datasource UIDs that jjx provisions, mapped to their Grafana
    # datasource type and display name.
    datasource_uids = {
        "prometheusds": ("prometheus", "Prometheus"),
        "lokids": ("loki", "Loki"),
    }

    # Collect the datasource UID variable names referenced in the dashboard.
    referenced: set[str] = set()
    for panel in dashboard.get("panels", []):
        datasource = panel.get("datasource")
        if isinstance(datasource, dict):
            uid = datasource.get("uid", "")
        else:
            uid = datasource if isinstance(datasource, str) else ""
        if uid.startswith("${") and uid.endswith("}"):
            name = uid[2:-1]
            if name in datasource_uids:
                referenced.add(name)

    if not referenced:
        return

    templating = dashboard.setdefault("templating", {})
    existing = {v.get("name") for v in templating.get("list", [])}

    for name in sorted(referenced):
        if name in existing:
            continue
        ds_type, ds_text = datasource_uids[name]
        templating.setdefault("list", []).append(
            {
                "name": name,
                "type": "datasource",
                "query": ds_type,
                "current": {"text": ds_text, "value": name},
                "hide": 2,
            }
        )


def populate_relation(
    model_state: dict[str, Any],
    relation: dict[str, Any],
    provider_app: str,
    grafana_info: dict[str, Any],
) -> None:
    """Read dashboards from the charm's relation databag and import into Grafana.

    Also configures Grafana's datasources to point at Prometheus and Loki
    (if they exist in the same model).
    """
    # Find the charm's app name (the non-virtual side of the relation).
    charm_app = None
    for app_name in relation.get("endpoints", {}):
        if app_name != provider_app:
            charm_app = app_name
            break
    if charm_app is None:
        return

    # Find Prometheus and Loki URLs in the same model for datasource config
    from . import _virtual_registry

    prometheus_url = ""
    loki_url = ""
    for app_name, app_state in model_state.get("apps", {}).items():
        if not app_state.get("virtual"):
            continue
        virtual_kind = app_state.get("virtual_kind")
        spec = _virtual_registry.get_spec(virtual_kind or "")
        if spec is None or spec.display_name is None:
            continue
        info = app_state.get(spec.info_key, {})
        url = _virtual_registry.resolve_endpoint_url(info, spec.default_port)
        if not url:
            continue
        if spec.display_name == "Prometheus":
            prometheus_url = url
        elif spec.display_name == "Loki API":
            loki_url = url

    # Update datasources
    update_datasources(grafana_info, prometheus_url, loki_url)

    # Import dashboards from the charm's relation databag
    import_dashboards(grafana_info, relation, charm_app)
