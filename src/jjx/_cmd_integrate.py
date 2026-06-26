"""Integrate command wrapper.

Creates a relation between two applications. If one side is a virtual charm
(e.g. postgresql-k8s), the virtual provider populates the relation data before
firing relation events on the real charm.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _engine, _virtual_postgres


def _parse_app_endpoint(token: str) -> tuple[str, str | None]:
    """Parse 'app' or 'app:endpoint' into (app, endpoint_or_none)."""
    if ":" in token:
        app, _, endpoint = token.partition(":")
        return app, endpoint or None
    return token, None


# Endpoint metadata for virtual charms. These mimic what the real charm's
# charmcraft.yaml would declare.
_VIRTUAL_ENDPOINTS = {
    "postgresql": {
        "database": {"interface": "postgresql_client", "role": "provides"},
    },
}


def _charm_endpoints(app_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read requires/provides from a charm's charmcraft.yaml.

    Returns a dict mapping endpoint name -> {"interface": ..., "role": "requires"|"provides"}.
    """
    virtual_kind = app_state.get("virtual_kind")
    if virtual_kind:
        return _VIRTUAL_ENDPOINTS.get(virtual_kind, {}).copy()

    charm_source = app_state.get("charm_source", "")
    if not charm_source:
        return {}
    data = _engine._read_yaml(Path(charm_source) / "charmcraft.yaml")
    if not data:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for role_key in ("requires", "provides", "peers"):
        for ep_name, ep_spec in (data.get(role_key) or {}).items():
            if isinstance(ep_spec, dict) and "interface" in ep_spec:
                result[ep_name] = {"interface": ep_spec["interface"], "role": role_key}
    return result


def _match_endpoints(
    app1: str,
    ep1: str | None,
    app2: str,
    ep2: str | None,
    model_state: dict[str, Any],
) -> tuple[str, str, str]:
    """Find matching endpoints between two apps.

    Returns (endpoint1, endpoint2, interface).
    """
    apps = model_state.get("apps", {})
    app1_state = apps.get(app1, {})
    app2_state = apps.get(app2, {})

    eps1 = _charm_endpoints(app1_state)
    eps2 = _charm_endpoints(app2_state)

    # If both endpoints are specified, use them directly.
    if ep1 and ep2:
        interface = eps1.get(ep1, {}).get("interface") or eps2.get(ep2, {}).get("interface", "")
        return ep1, ep2, interface

    # Try to match by interface.
    candidates = []
    for name1, spec1 in eps1.items():
        for name2, spec2 in eps2.items():
            if spec1["interface"] == spec2["interface"]:
                candidates.append((name1, name2, spec1["interface"]))

    if ep1:
        candidates = [c for c in candidates if c[0] == ep1]
    if ep2:
        candidates = [c for c in candidates if c[1] == ep2]

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise _engine.CliError(f"no matching endpoints found between {app1} and {app2}")
    # Multiple matches — ambiguous.
    raise _engine.CliError(
        f"multiple matching endpoints between {app1} and {app2}: "
        f"{', '.join(f'{c[0]}:{c[1]}' for c in candidates)}"
    )


def integrate(args: list[str], model: str | None) -> int:
    """Execute the integrate command."""
    if not args:
        raise _engine.CliError("usage: juju integrate <app1>[:<ep1>] <app2>[:<ep2>]")

    # Filter out --via and other flags.
    positionals: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--via" and i + 1 < len(args):
            i += 2
            continue
        if token.startswith("--via="):
            i += 1
            continue
        if token.startswith("--"):
            i += 1
            continue
        positionals.append(token)
        i += 1

    if len(positionals) < 2:
        raise _engine.CliError("usage: juju integrate <app1>[:<ep1>] <app2>[:<ep2>]")

    app1, ep1 = _parse_app_endpoint(positionals[0])
    app2, ep2 = _parse_app_endpoint(positionals[1])

    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    apps = model_state.get("apps", {})
    if app1 not in apps:
        raise _engine.CliError(f"application {app1} not found")
    if app2 not in apps:
        raise _engine.CliError(f"application {app2} not found")

    ep1_name, ep2_name, interface = _match_endpoints(app1, ep1, app2, ep2, model_state)

    relation_id = _engine._next_relation_id(model_state)
    relation = {
        "id": relation_id,
        "interface": interface,
        "endpoints": {app1: ep1_name, app2: ep2_name},
        "data": {
            app1: {"app": {}, f"{app1}/0": {}},
            app2: {"app": {}, f"{app2}/0": {}},
        },
    }
    _engine._relations(model_state).append(relation)
    _engine._append_log(
        model_state, f"relation {relation_id} created: {app1}:{ep1_name} <-> {app2}:{ep2_name}"
    )

    # Determine which app is the virtual provider (if any).
    app1_virtual = apps[app1].get("virtual_kind")
    app2_virtual = apps[app2].get("virtual_kind")

    if app1_virtual == "postgresql":
        _virtual_postgres.populate_relation(model_state, relation, app1, apps[app1]["pg_info"])
    elif app2_virtual == "postgresql":
        _virtual_postgres.populate_relation(model_state, relation, app2, apps[app2]["pg_info"])

    _engine._save_state(state)

    # Fire relation-created then relation-changed on the real charm(s).
    # The real charm is the non-virtual one.
    real_app = app2 if app1_virtual else app1
    if apps[real_app].get("virtual"):
        # Both virtual? Nothing to do.
        return 0

    # Reload state to get the relation with populated data.
    state = _engine._load_state()
    relation = _engine._find_relation_by_id(state["models"][model_name], relation_id)
    assert relation is not None

    _engine._run_relation_event_flow(model_name, real_app, relation, event="created")
    _engine._run_relation_event_flow(model_name, real_app, relation, event="changed")

    return 0
