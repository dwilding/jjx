"""Integrate command wrapper.

Creates a relation between two applications. If one side is a virtual charm
(e.g. postgresql-k8s), the virtual provider populates the relation data before
firing relation events on the real charm.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _engine, _virtual_registry


def _parse_app_endpoint(token: str) -> tuple[str, str | None]:
    """Parse 'app' or 'app:endpoint' into (app, endpoint_or_none)."""
    if ":" in token:
        app, _, endpoint = token.partition(":")
        return app, endpoint or None
    return token, None


def _charm_endpoints(app_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read requires/provides from a charm's charmcraft.yaml.

    Returns a dict mapping endpoint name -> {"interface": ..., "role": "requires"|"provides"}.
    """
    virtual_kind = app_state.get("virtual_kind")
    if virtual_kind:
        spec = _virtual_registry.get_spec(virtual_kind)
        if spec is not None:
            return dict(spec.endpoints)
        return {}

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

    # Check for cross-model references: "model.app" or "admin/model.app".
    # In jjx, cross-model means the app lives in a different model in the
    # same state.json. We resolve the reference and treat it as a remote app.
    cross_model_info = None  # (remote_model_name, remote_app_name) if cross-model
    local_app = None
    local_ep = None
    remote_ep = None

    for app_ref, ep_ref in [(app1, ep1), (app2, ep2)]:
        cross = _parse_cross_model_ref(app_ref)
        if cross is not None:
            remote_model_name, remote_app_name = cross
            cross_model_info = (remote_model_name, remote_app_name)
            remote_ep = ep_ref
        else:
            local_app = app_ref
            local_ep = ep_ref

    if cross_model_info is not None:
        # Cross-model integration: the local app is in the current model,
        # the remote app is in another model (accessed via an offer).
        if local_app is None:
            raise _engine.CliError("no local application specified for cross-model integration")
        return _integrate_cross_model(
            state,
            model_name,
            model_state,
            local_app,
            local_ep,
            cross_model_info[0],
            cross_model_info[1],
            remote_ep,
        )

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

    # Use the registry to populate relation data for whichever app is virtual.
    for virtual_app, virtual_kind in [(app1, app1_virtual), (app2, app2_virtual)]:
        if virtual_kind is None:
            continue
        spec = _virtual_registry.get_spec(virtual_kind)
        if spec is not None:
            info = apps[virtual_app].get(spec.info_key, {})
            spec.populate(model_state, relation, virtual_app, info)
            break

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
    _engine._run_relation_event_flow(model_name, real_app, relation, event="joined")
    _engine._run_relation_event_flow(model_name, real_app, relation, event="changed")

    return 0


def _parse_cross_model_ref(ref: str) -> tuple[str, str] | None:
    """Parse a cross-model reference like 'model.app' or 'admin/model.app'.

    Returns (model_name, app_name) if it's a cross-model ref, or None if not.
    A plain app name (no dots, no slashes) is not cross-model.
    """
    # Handle "admin/model.app" — strip the controller prefix.
    if "/" in ref:
        _, _, ref = ref.partition("/")

    # Now check for "model.app" — a dot means cross-model.
    if "." in ref:
        model_name, _, app_name = ref.partition(".")
        if model_name and app_name:
            return model_name, app_name

    return None


def _integrate_cross_model(
    state: dict[str, Any],
    local_model_name: str,
    local_model_state: dict[str, Any],
    local_app: str,
    local_ep: str | None,
    remote_model_name: str,
    remote_app_name: str,
    remote_ep: str | None,
) -> int:
    """Handle cross-model integration.

    The local app is in ``local_model_name``, the remote app is in
    ``remote_model_name``. We create the relation in the local model and
    populate the remote app's databag from the remote model's state.
    """
    local_apps = local_model_state.get("apps", {})
    if local_app not in local_apps:
        raise _engine.CliError(f"application {local_app} not found")

    remote_model_state = state.get("models", {}).get(remote_model_name)
    if remote_model_state is None:
        raise _engine.CliError(f"ERROR model {remote_model_name} does not exist")

    remote_apps = remote_model_state.get("apps", {})
    if remote_app_name not in remote_apps:
        raise _engine.CliError(
            f"application {remote_app_name} not found in model {remote_model_name}"
        )

    remote_app_state = remote_apps[remote_app_name]

    # Match endpoints: the local app's endpoints come from its charmcraft.yaml,
    # the remote app's endpoints come from the virtual endpoint metadata (if
    # virtual) or its charmcraft.yaml.
    local_eps = _charm_endpoints(local_apps[local_app])
    remote_eps = _charm_endpoints(remote_app_state)

    # Try to match by interface.
    candidates = []
    for name1, spec1 in local_eps.items():
        for name2, spec2 in remote_eps.items():
            if spec1["interface"] == spec2["interface"]:
                candidates.append((name1, name2, spec1["interface"]))

    if local_ep:
        candidates = [c for c in candidates if c[0] == local_ep]
    if remote_ep:
        candidates = [c for c in candidates if c[1] == remote_ep]

    if len(candidates) == 1:
        ep1_name, ep2_name, interface = candidates[0]
    elif not candidates:
        raise _engine.CliError(
            f"no matching endpoints found between {local_app} and {remote_model_name}.{remote_app_name}"
        )
    else:
        raise _engine.CliError(
            f"multiple matching endpoints between {local_app} and {remote_model_name}.{remote_app_name}: "
            f"{', '.join(f'{c[0]}:{c[1]}' for c in candidates)}"
        )

    relation_id = _engine._next_relation_id(local_model_state)
    relation = {
        "id": relation_id,
        "interface": interface,
        "endpoints": {local_app: ep1_name, remote_app_name: ep2_name},
        "data": {
            local_app: {"app": {}, f"{local_app}/0": {}},
            remote_app_name: {"app": {}, f"{remote_app_name}/0": {}},
        },
        # Mark this as a cross-model relation so the hook tools know the
        # remote app lives in a different model.
        "cross_model": {
            "remote_model": remote_model_name,
            "remote_app": remote_app_name,
        },
    }
    _engine._relations(local_model_state).append(relation)
    _engine._append_log(
        local_model_state,
        f"cross-model relation {relation_id} created: {local_app}:{ep1_name} <-> {remote_model_name}.{remote_app_name}:{ep2_name}",
    )

    # Populate the remote app's databag from the virtual provider.
    # Pass the remote model state (where the virtual app lives) so the
    # provider can find sibling virtual apps (e.g. grafana needs Prometheus
    # and Loki, which live in the same COS model as grafana).
    remote_virtual = remote_app_state.get("virtual_kind")
    if remote_virtual is not None:
        spec = _virtual_registry.get_spec(remote_virtual)
        if spec is not None:
            info = remote_app_state.get(spec.info_key, {})
            spec.populate(remote_model_state, relation, remote_app_name, info)

    _engine._save_state(state)

    # Fire relation-created then relation-changed on the local (real) charm.
    if local_apps[local_app].get("virtual"):
        return 0

    # Reload state to get the relation with populated data.
    state = _engine._load_state()
    relation = _engine._find_relation_by_id(state["models"][local_model_name], relation_id)
    assert relation is not None

    _engine._run_relation_event_flow(local_model_name, local_app, relation, event="created")
    _engine._run_relation_event_flow(local_model_name, local_app, relation, event="joined")
    _engine._run_relation_event_flow(local_model_name, local_app, relation, event="changed")

    # Re-populate the remote (virtual) app's relation data after the charm
    # has had a chance to write to its databag during relation-changed.
    # This is needed for relations like grafana-dashboard where the virtual
    # charm reads data the charm writes during the relation-changed event.
    # Use the remote model state so the provider can find sibling virtual
    # apps (e.g. grafana needs Prometheus and Loki from its own COS model).
    if remote_virtual is not None:
        spec = _virtual_registry.get_spec(remote_virtual)
        if spec is not None:
            state = _engine._load_state()
            local_model_state = state["models"][local_model_name]
            remote_model_state = state["models"][remote_model_name]
            relation = _engine._find_relation_by_id(local_model_state, relation_id)
            assert relation is not None
            remote_app_state = remote_model_state["apps"][remote_app_name]
            info = remote_app_state.get(spec.info_key, {})
            spec.populate(remote_model_state, relation, remote_app_name, info)
            _engine._save_state(state)

    return 0
