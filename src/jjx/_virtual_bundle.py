"""Virtual bundle deployer for cos-lite.

When ``juju deploy cos-lite`` is called, this module deploys the COS Lite
components as virtual charms in the target model. Only the components needed
for integration testing are deployed — currently loki and traefik.

In real COS Lite, the bundle deploys 6 charms (prometheus, loki, grafana,
alertmanager, catalogue, traefik) with relations between them. In jjx, we
deploy only what's needed for the integration tests to pass: loki (for log
forwarding) and traefik (for the ``show-proxied-endpoints`` action that tests
use to discover Loki's URL).
"""

from __future__ import annotations

from typing import Any

from . import _engine, _virtual_registry


# Virtual bundles: map bundle name to the list of (app_name, virtual_kind)
# components that should be deployed.
_VIRTUAL_BUNDLES = {
    "cos-lite": [
        ("loki", "loki"),
        ("prometheus", "prometheus"),
        ("grafana", "grafana"),
        ("traefik", "traefik"),
    ],
}


def is_virtual_bundle(charm_path: str) -> bool:
    """Check if the given charm path is a virtual bundle."""
    return charm_path in _VIRTUAL_BUNDLES


def deploy_bundle(
    state: dict[str, Any],
    model_name: str,
    bundle_name: str,
) -> int:
    """Deploy a virtual bundle (e.g. cos-lite) in the target model.

    Deploys each component as a virtual charm and sets them all to active.
    """
    components = _VIRTUAL_BUNDLES.get(bundle_name)
    if components is None:
        raise _engine.CliError(f"unknown virtual bundle: {bundle_name}")

    model_state = state["models"][model_name]

    for app_name, virtual_kind in components:
        spec = _virtual_registry.get_spec(virtual_kind)
        if spec is None:
            raise _engine.CliError(f"unknown virtual kind in bundle: {virtual_kind}")
        info = spec.start(model_name, app_name)
        model_state["apps"][app_name] = _virtual_registry.make_app_state(
            virtual_kind, app_name, info
        )
        _engine._append_log(
            model_state,
            f"virtual {app_name} deployed ({virtual_kind} at {info.get('ip_address', 'no IP')})",
        )

    _engine._save_state(state)
    return 0
