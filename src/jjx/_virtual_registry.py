"""Virtual charm registry.

This module provides a single registry for all virtual charms. Each virtual
charm module registers itself here, providing the functions needed to deploy
it, populate relation data, and (optionally) expose a user-facing endpoint.

This centralises the virtual charm dispatch so that ``_cmd_deploy``,
``_cmd_integrate``, ``_virtual_bundle``, and ``_cli`` all use the same
registry instead of maintaining separate if/elif chains.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Type aliases for virtual charm functions.
StartFunc = Callable[[str, str], dict[str, Any]]
PopulateFunc = Callable[[dict[str, Any], dict[str, Any], str, dict[str, Any]], None]


@dataclass(frozen=True)
class VirtualCharmSpec:
    """Specification for a virtual charm."""

    kind: str
    """Short name for this virtual charm kind (e.g. ``"loki"``)."""

    start: StartFunc
    """Function to start the workload container and return provider state."""

    populate: PopulateFunc
    """Function to write provider-side relation data on integrate."""

    info_key: str
    """Key in app state where the provider info dict is stored
    (e.g. ``"loki_info"``, ``"pg_info"``)."""

    endpoints: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    """Endpoint metadata mimicking charmcraft.yaml's requires/provides.
    Maps endpoint name -> {"interface": ..., "role": "requires"|"provides"}."""

    display_name: str | None = None
    """Human-readable name for endpoint display, or None if no endpoint."""

    default_port: int = 0
    """Default port for endpoint display (used when info dict has no port)."""

    teardown_priority: int = 50
    """Teardown ordering priority. Lower = removed first.
    COS charms use 10-30, postgres uses 40, workload uses 50, charm runner 60."""


# Registry: virtual_kind -> spec
_REGISTRY: dict[str, VirtualCharmSpec] = {}


def register(spec: VirtualCharmSpec) -> None:
    """Register a virtual charm spec."""
    _REGISTRY[spec.kind] = spec


def get_spec(kind: str) -> VirtualCharmSpec | None:
    """Look up a virtual charm spec by kind."""
    return _REGISTRY.get(kind)


def all_kinds() -> list[str]:
    """Return all registered virtual charm kinds."""
    return list(_REGISTRY.keys())


def make_app_state(
    kind: str,
    app_name: str,
    info: dict[str, Any],
) -> dict[str, Any]:
    """Create the standard app state dict for a virtual charm.

    All virtual charms share the same state structure — this ensures
    consistency and avoids duplicating the dict literal in every deploy path.
    """
    spec = get_spec(kind)
    assert spec is not None, f"unknown virtual charm kind: {kind}"
    return {
        "charm": app_name,
        "charm_source": "",
        "virtual": True,
        "virtual_kind": kind,
        "resources": {},
        "config": {},
        "container_name": info.get("container_name", ""),
        "container_id": info.get("container_id", ""),
        "unit": f"{app_name}/0",
        "workload": "",
        spec.info_key: info,
        "unit_status": _make_active_status(),
        "app_status": _make_active_status(),
        "updated_at": _now_iso(),
    }


def _make_active_status() -> dict[str, Any]:
    """Create an active status dict."""
    return {"status": "active", "message": "", "status-data": {}}


def resolve_endpoint_url(info: dict[str, Any], default_port: int) -> str:
    """Resolve the URL for a virtual charm's HTTP endpoint.

    Refreshes the container IP in case it changed since deploy time, then
    returns ``http://<ip>:<port>`` or empty string if the container is not
    running. Also mutates ``info`` to update the cached host/IP.
    """
    container_name = info.get("container_name", "")
    host = info.get("host", "")
    port = info.get("port", default_port)
    if container_name:
        try:
            from . import _engine

            details = _engine._docker_container_details(container_name)
            if details.running and details.ip_address:
                host = details.ip_address
                info["host"] = details.ip_address
                info["ip_address"] = details.ip_address
        except Exception:  # noqa: BLE001, S110
            pass
    if host:
        return f"http://{host}:{port}"
    return ""


def _now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
