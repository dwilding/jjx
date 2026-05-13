"""Status command wrapper."""

from __future__ import annotations

import json
import sys

from . import _engine


def status(args: list[str], model: str | None) -> int:
    """Execute the status command."""
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    as_json = False
    i = 0
    while i < len(args):
        if args[i] == "--format" and i + 1 < len(args):
            as_json = args[i + 1] == "json"
            i += 2
            continue
        i += 1

    applications = {
        app_name: _engine._status_for_app(app_name, app_state)
        for app_name, app_state in model_state.get("apps", {}).items()
    }

    payload = {
        "model": {
            "name": model_name,
            "type": "caas",
            "controller": "jjx",
            "cloud": "localhost",
            "version": "3.6.0",
            "model-status": {
                "current": "available",
                "message": "available",
            },
        },
        "machines": {},
        "applications": applications,
    }

    if as_json:
        sys.stdout.write(json.dumps(payload))
    else:
        for app_name, app in applications.items():
            app_status = app.get("application-status", {}).get("current", "unknown")
            sys.stdout.write(f"{app_name}\t{app_status}\n")
    return 0
