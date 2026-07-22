"""Virtual traefik-k8s provider.

This module implements a minimal "virtual charm" for traefik-k8s that exists
solely to support the ``show-proxied-endpoints`` action, which COS Lite
integration tests use to discover the URLs of proxied applications (like Loki).

In real COS Lite, Traefik is an ingress/load-balancer that proxies HTTP traffic
to the COS applications. In jjx, containers are directly reachable by bridge
IP, so we don't need actual proxying — we just need the action to return the
correct URLs so tests can query Loki's HTTP API directly.

The virtual traefik has no container — it's a pure state entry that responds
to the ``show-proxied-endpoints`` action with the URLs of the other virtual
charms in the model.
"""

from __future__ import annotations

from typing import Any


def start_traefik(
    model_name: str,
    app_name: str,
) -> dict[str, Any]:
    """Register a virtual traefik app (no container needed).

    Returns a dict with the app state fields.
    """
    return {
        "container_name": "",
        "container_id": "",
        "ip_address": "",
        "host": "",
        "port": 0,
    }


def get_proxied_endpoints(model_state: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return the proxied endpoints for all virtual charms in the model.

    This mimics the output of Traefik's ``show-proxied-endpoints`` action.
    For each virtual charm that has a display name (i.e. has an HTTP endpoint),
    we return its direct URL since there's no actual proxying in jjx.
    """
    from . import _virtual_registry

    endpoints: dict[str, dict[str, str]] = {}
    for app_name, app_state in model_state.get("apps", {}).items():
        if not app_state.get("virtual"):
            continue
        virtual_kind = app_state.get("virtual_kind")
        spec = _virtual_registry.get_spec(virtual_kind or "")
        if spec is None or spec.display_name is None:
            continue
        info = app_state.get(spec.info_key, {})
        url = _virtual_registry.resolve_endpoint_url(info, spec.default_port)
        if url:
            endpoints[f"{app_name}/0"] = {
                "url": url,
                "mode": "http",
            }
    return endpoints
