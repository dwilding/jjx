"""Run command wrapper (actions).

Executes an action on a unit. For real charms, this would run the action
hook via ``docker exec`` into the charm runner (not yet implemented). For
virtual charms, it returns dynamically-computed results (e.g. traefik's
``show-proxied-endpoints`` discovers the URLs of other COS charms in the model).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import _engine, _virtual_traefik


def run(args: list[str], model: str | None) -> int:
    """Execute the run command.

    Usage: juju run [--format json] <unit> <action> [--wait <timeout>s] [--params <file>]
    """
    # Parse arguments.
    output_format = "json"
    unit: str | None = None
    action: str | None = None

    i = 0
    while i < len(args):
        token = args[i]
        if token == "--format" and i + 1 < len(args):
            output_format = args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token == "--wait" and i + 1 < len(args):
            i += 2
            continue
        if token.startswith("--wait="):
            i += 1
            continue
        if token == "--params" and i + 1 < len(args):
            i += 2
            continue
        if token.startswith("--"):
            i += 1
            continue
        if unit is None:
            unit = token
        elif action is None:
            action = token
        i += 1

    if unit is None or action is None:
        raise _engine.CliError("usage: juju run <unit> <action>")

    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    # Parse unit name: "app/0" or "app/leader"
    if "/" not in unit:
        raise _engine.CliError(f"invalid unit name: {unit}")
    app_name, _, unit_id = unit.partition("/")

    apps = model_state.get("apps", {})
    if app_name not in apps:
        raise _engine.CliError(f"application {app_name} not found")

    app_state = apps[app_name]

    # Handle virtual charm actions.
    if app_state.get("virtual"):
        return _run_virtual_action(model_state, app_name, app_state, action, output_format)

    # For real charms, we would need to run the action hook. This is not
    # implemented yet — the k8s-5-observe tests only use actions on traefik
    # (virtual) and the charm's own get-db-info action (which the tests
    # don't call in the COS Lite test flow).
    raise _engine.CliError(f"actions on real charms not yet supported: {action}")


def _run_virtual_action(
    model_state: dict[str, Any],
    app_name: str,
    app_state: dict[str, Any],
    action: str,
    output_format: str,
) -> int:
    """Handle actions on virtual charms."""
    virtual_kind = app_state.get("virtual_kind")

    if virtual_kind == "traefik" and action == "show-proxied-endpoints":
        endpoints = _virtual_traefik.get_proxied_endpoints(model_state)
        # The action result format jubilant expects:
        # {"<unit>": {"id": "...", "status": "completed", "results": {...}}}
        unit_name = f"{app_name}/0"
        result = {
            unit_name: {
                "id": "1",
                "status": "completed",
                "results": {
                    "proxied-endpoints": json.dumps(endpoints),
                    "return-code": 0,
                    "stdout": "",
                    "stderr": "",
                },
            }
        }
        sys.stdout.write(json.dumps(result))
        return 0

    raise _engine.CliError(f"action {action} not supported on virtual {virtual_kind}")
