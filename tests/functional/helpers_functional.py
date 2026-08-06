import json
import subprocess
import urllib.error
import urllib.request

import jjx


def assert_container(container_name: str) -> None:
    command = [
        jjx.container_runtime(),
        "inspect",
        container_name,
    ]
    subprocess.run(
        command,
        check=True,
    )


def assert_no_container(container_name: str) -> None:
    command = [
        jjx.container_runtime(),
        "inspect",
        container_name,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    # Docker returns 1. Podman returns 125.
    assert result.returncode != 0, f"container {container_name} still exists"


def assert_loki_config(*, loki_ip: str, label: str, value: str) -> None:
    url = f"http://{loki_ip}:3100/loki/api/v1/label/{label}/values"
    data = fetch_dict(url)
    assert data["status"] == "success", f"Loki label query for {label} failed: {data}"
    values = data.get("data", [])
    assert value in values, f"Loki has no logs with {label}={value}; found: {values}"


def assert_prometheus_config(*, prometheus_ip: str, workload_ip: str, metric: str) -> None:
    # Prometheus is scraping the workload.
    url = f"http://{prometheus_ip}:9090/api/v1/targets"
    data = fetch_dict(url)
    targets = data.get("data", {}).get("activeTargets", [])
    workload_targets = [t for t in targets if workload_ip in t.get("scrapeUrl", "")]
    assert workload_targets, (
        f"Prometheus has no scrape target for workload at {workload_ip}; "
        f"active targets: {[t.get('scrapeUrl') for t in targets]}"
    )
    target = workload_targets[0]
    assert target["health"] == "up", (
        f"Prometheus scrape target for {workload_ip} is {target['health']}, expected 'up'"
    )
    # Prometheus has collected the metric.
    url = f"http://{prometheus_ip}:9090/api/v1/query?query={metric}"
    data = fetch_dict(url)
    assert data["status"] == "success", f"Prometheus query for {metric} failed: {data}"
    result = data.get("data", {}).get("result", [])
    assert result, f"Prometheus has no data for metric '{metric}'"


def assert_grafana_config(
    *, grafana_ip: str, datasources: dict[str, str], dashboard_title: str
) -> None:
    # Grafana has the expected datasources.
    url = f"http://{grafana_ip}:3000/api/datasources"
    data = fetch_dict(url)
    for uid, ds_type in datasources.items():
        matching = [ds for ds in data if ds.get("uid") == uid]
        assert matching, (
            f"Grafana has no datasource with uid={uid}; found: {[ds.get('uid') for ds in data]}"
        )
        ds = matching[0]
        assert ds["type"] == ds_type, (
            f"Grafana datasource uid={uid} is type={ds['type']}, expected {ds_type}"
        )
    # Grafana has the dashboard imported.
    url = f"http://{grafana_ip}:3000/api/search?type=dash-db"
    data = fetch_dict(url)
    matching = [d for d in data if d.get("title") == dashboard_title]
    assert matching, (
        f"Grafana has no dashboard titled '{dashboard_title}'; "
        f"found: {[d.get('title') for d in data]}"
    )


def container_ip(container_name: str) -> str:
    command = [
        jjx.container_runtime(),
        "inspect",
        "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        container_name,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"inspect exited with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip()


def fetch_dict(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())
