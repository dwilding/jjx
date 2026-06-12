"""Internal hook-tool command wrapper."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import yaml

from . import _engine


def _tool_context() -> tuple[dict[str, Any], str, str, dict[str, Any], dict[str, Any]]:
    model_name = os.environ.get("JUJU_MODEL_NAME")
    app_name = None
    unit_name = os.environ.get("JUJU_UNIT_NAME", "")
    if "/" in unit_name:
        app_name = unit_name.split("/", 1)[0]

    state = _engine._load_state()
    models = state.get("models", {})
    model_state = models.get(model_name) if model_name else None

    if model_state is None and len(models) == 1:
        model_name, model_state = next(iter(models.items()))

    if model_state is None:
        raise _engine.CliError(f"model {model_name or '<unset>'} not found")

    apps = model_state.get("apps", {})
    app_state = apps.get(app_name) if app_name else None
    if app_state is None and len(apps) == 1:
        app_name, app_state = next(iter(apps.items()))

    if app_state is None:
        raise _engine.CliError(f"application {app_name or '<unset>'} not found")

    assert isinstance(model_name, str)
    assert isinstance(app_name, str)
    assert isinstance(model_state, dict)
    assert isinstance(app_state, dict)

    return state, model_name, app_name, model_state, app_state


def hook_tool(args: list[str]) -> int:
    """Execute the internal ``_hook-tool`` command."""
    if not args:
        raise _engine.CliError("usage: juju _hook-tool <tool> [args]")

    tool = args[0]
    tool_args = args[1:]
    state, _model_name, app_name, model_state, app_state = _tool_context()

    if tool == "config-get":
        output_format = "json"
        all_keys = False
        key: str | None = None
        i = 0
        while i < len(tool_args):
            token = tool_args[i]
            if token == "--format":
                if i + 1 < len(tool_args):
                    output_format = tool_args[i + 1]
                i += 2
                continue
            if token.startswith("--format="):
                output_format = token.split("=", 1)[1]
                i += 1
                continue
            if token == "--all":
                all_keys = True
                i += 1
                continue
            if token.startswith("-"):
                i += 1
                continue
            key = token
            i += 1

        cfg = app_state.get("config", {})
        payload: Any
        if all_keys or key is None:
            payload = cfg
        else:
            payload = cfg.get(key)

        if output_format == "yaml":
            sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
        else:
            sys.stdout.write(json.dumps(payload))
        return 0

    if tool == "is-leader":
        sys.stdout.write("true\n")
        return 0

    if tool == "juju-log":
        level = "INFO"
        message_parts: list[str] = []
        i = 0
        while i < len(tool_args):
            token = tool_args[i]
            if token == "--log-level" and i + 1 < len(tool_args):
                level = tool_args[i + 1]
                i += 2
                continue
            if token == "--":
                message_parts.extend(tool_args[i + 1 :])
                break
            message_parts.append(token)
            i += 1

        message = " ".join(message_parts).strip()
        _engine._append_log(model_state, f"{app_name}:juju-log:{level}:{message}")
        _engine._save_state(state)
        return 0

    if tool == "status-set":
        is_app = False
        i = 0
        rest: list[str] = []
        while i < len(tool_args):
            token = tool_args[i]
            if token.startswith("--application="):
                val = token.split("=", 1)[1].strip().lower()
                is_app = val in {"true", "1", "yes"}
                i += 1
                continue
            rest.append(token)
            i += 1

        if not rest:
            raise _engine.CliError("status-set requires a status")

        status = rest[0]
        message = ""
        if "--" in rest:
            idx = rest.index("--")
            message = " ".join(rest[idx + 1 :])
        elif len(rest) > 1:
            message = " ".join(rest[1:])

        payload = _engine._status_dict(status, message)
        if is_app:
            app_state["app_status"] = payload
        else:
            app_state["unit_status"] = payload
            app_state["app_status"] = payload

        app_state["updated_at"] = _engine._now_iso()
        _engine._append_log(
            model_state,
            f"{app_name}:status-set:{'app' if is_app else 'unit'}:{status}:{message}",
        )
        _engine._save_state(state)
        return 0

    if tool == "status-get":
        is_app = False
        for token in tool_args:
            if token.startswith("--application="):
                val = token.split("=", 1)[1].strip().lower()
                is_app = val in {"true", "1", "yes"}

        app_payload = app_state.get("app_status") or _engine._status_dict(
            "maintenance", "initializing"
        )
        unit_payload = app_state.get("unit_status") or _engine._status_dict(
            "maintenance", "initializing"
        )
        unit_name = app_state.get("unit", f"{app_name}/0")

        if is_app:
            out = {
                "application-status": app_payload,
                "units": {unit_name: unit_payload},
            }
        else:
            out = unit_payload

        sys.stdout.write(json.dumps(out))
        return 0

    if tool == "application-version-set":
        # ops invokes: application-version-set -- <version>
        if tool_args and tool_args[0] == "--":
            version = " ".join(tool_args[1:]).strip()
        else:
            version = " ".join(tool_args).strip()
        app_state["workload_version"] = version
        app_state["updated_at"] = _engine._now_iso()
        _engine._save_state(state)
        return 0

    raise _engine.CliError(f"unsupported hook tool: {tool}")
