"""Config command wrapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import _engine


def config(args: list[str], model: str | None) -> int:
    """Execute the config command."""
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    if not args:
        raise _engine.CliError("usage: juju config <app> [k=v ...] [--reset key]")

    format_json = False
    parsed: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--format":
            if i + 1 >= len(args):
                raise _engine.CliError("option --format needs an argument")
            format_json = args[i + 1] == "json"
            i += 2
            continue
        parsed.append(args[i])
        i += 1

    app_name = parsed[0]
    if app_name not in model_state.get("apps", {}):
        raise _engine.CliError(f"application {app_name} not found")
    app_state = model_state["apps"][app_name]

    if len(parsed) == 1:
        cfg = app_state.get("config", {})
        payload = {k: {"type": "string", "value": str(v)} for k, v in cfg.items()}
        if format_json:
            sys.stdout.write(json.dumps({"settings": payload, "application-config": payload}))
        else:
            for key, value in sorted(cfg.items()):
                sys.stdout.write(f"{key}: {value}\n")
        return 0

    reset_keys: set[str] = set()
    assignments: dict[str, str] = {}
    j = 1
    while j < len(parsed):
        token = parsed[j]
        if token == "--reset":
            if j + 1 >= len(parsed):
                raise _engine.CliError("option --reset needs an argument")
            reset_keys.update(k.strip() for k in parsed[j + 1].split(",") if k.strip())
            j += 2
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            assignments[key] = value
        j += 1

    cfg = app_state.setdefault("config", {})
    defaults = _engine._default_config(Path(app_state["charm_source"]))
    for key, value in assignments.items():
        cfg[key] = value
    for key in reset_keys:
        if key in defaults:
            cfg[key] = defaults[key]
        else:
            cfg.pop(key, None)

    app_state["updated_at"] = _engine._now_iso()
    _engine._save_state(state)
    _engine._run_config_event_flow(model_name, app_name)
    return 0
