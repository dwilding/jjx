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
    unit_name = os.environ.get("JUJU_UNIT_NAME", f"{app_name}/0")

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

    if tool == "relation-ids":
        return _relation_ids(tool_args, model_state)

    if tool == "relation-list":
        return _relation_list(tool_args, model_state, app_name)

    if tool == "relation-get":
        return _relation_get(tool_args, model_state, app_name, unit_name)

    if tool == "relation-set":
        return _relation_set(tool_args, model_state, app_name, unit_name, state)

    if tool == "relation-model-get":
        return _relation_model_get(tool_args, model_state)

    if tool == "secret-add":
        return _secret_add(tool_args, model_state, app_name, state)

    if tool == "secret-get":
        return _secret_get(tool_args, model_state)

    if tool == "secret-grant":
        return _secret_grant(tool_args, model_state, state)

    if tool == "secret-info-get":
        return _secret_info_get(tool_args, model_state)

    if tool == "secret-ids":
        return _secret_ids(model_state, app_name)

    if tool == "secret-remove":
        return _secret_remove(tool_args, model_state, state)

    if tool == "secret-revoke":
        return _secret_revoke(tool_args, model_state, state)

    if tool == "secret-set":
        return _secret_set(tool_args, model_state, state)

    raise _engine.CliError(f"unsupported hook tool: {tool}")


# ---------------------------------------------------------------------------
# Relation hook tools
# ---------------------------------------------------------------------------


def _parse_relation_ref(token: str) -> tuple[str | None, int | None]:
    """Parse a -r argument like 'database:0' or '0'.

    Returns (endpoint, relation_id). Either may be None.
    """
    if ":" in token:
        endpoint, _, id_str = token.rpartition(":")
        try:
            return endpoint or None, int(id_str)
        except ValueError:
            return token, None
    try:
        return None, int(token)
    except ValueError:
        return token, None


def _resolve_relation_from_env(model_state: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the current relation from JUJU_RELATION_ID env var."""
    raw = os.environ.get("JUJU_RELATION_ID", "")
    if not raw:
        return None
    _, relation_id = _parse_relation_ref(raw)
    if relation_id is None:
        return None
    return _engine._find_relation_by_id(model_state, relation_id)


def _output(payload: Any, output_format: str = "json") -> None:
    if output_format == "yaml":
        sys.stdout.write(yaml.safe_dump(payload, sort_keys=False))
    else:
        sys.stdout.write(json.dumps(payload))


def _relation_ids(tool_args: list[str], model_state: dict[str, Any]) -> int:
    """relation-ids <endpoint> [--format=json]"""
    output_format = "json"
    endpoint: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--format" and i + 1 < len(tool_args):
            output_format = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        endpoint = token
        i += 1

    if endpoint is None:
        raise _engine.CliError("relation-ids requires an endpoint name")

    result = []
    for rel in _engine._relations(model_state):
        for app_name, ep in rel.get("endpoints", {}).items():
            if ep == endpoint:
                result.append(f"{endpoint}:{rel['id']}")
                break

    _output(result, output_format)
    return 0


def _relation_list(tool_args: list[str], model_state: dict[str, Any], local_app: str) -> int:
    """relation-list [-r <ref>] [--app] [--format=json]"""
    output_format = "json"
    is_app = False
    relation_id: int | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--format" and i + 1 < len(tool_args):
            output_format = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token == "-r" and i + 1 < len(tool_args):
            _, relation_id = _parse_relation_ref(tool_args[i + 1])
            i += 2
            continue
        if token.startswith("-r"):
            _, relation_id = _parse_relation_ref(token[2:])
            i += 1
            continue
        if token == "--app":
            is_app = True
            i += 1
            continue
        i += 1

    if relation_id is None:
        rel = _resolve_relation_from_env(model_state)
    else:
        rel = _engine._find_relation_by_id(model_state, relation_id)
    if rel is None:
        raise _engine.CliError("relation not found")

    if is_app:
        remote_app = _engine._relation_remote_app(rel, local_app)
        _output(remote_app if remote_app else "", output_format)
    else:
        # List remote units. In jjx's single-unit model, the remote app has one unit.
        remote_app = _engine._relation_remote_app(rel, local_app)
        units = [f"{remote_app}/0"] if remote_app else []
        _output(units, output_format)
    return 0


def _relation_get(
    tool_args: list[str],
    model_state: dict[str, Any],
    local_app: str,
    local_unit: str,
) -> int:
    """relation-get [--format=json] [-r <ref>] [--app] [key] [unit]"""
    output_format = "json"
    is_app = False
    relation_id: int | None = None
    key: str | None = None
    unit: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--format" and i + 1 < len(tool_args):
            output_format = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token == "-r" and i + 1 < len(tool_args):
            _, relation_id = _parse_relation_ref(tool_args[i + 1])
            i += 2
            continue
        if token.startswith("-r"):
            _, relation_id = _parse_relation_ref(token[2:])
            i += 1
            continue
        if token == "--app":
            is_app = True
            i += 1
            continue
        if token == "-":
            # "-" means "all keys" in Juju relation-get
            if key is None:
                key = "-"
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        # Positional args: key and unit (key first, then unit)
        if key is None:
            key = token
        elif unit is None:
            unit = token
        i += 1

    if relation_id is None:
        rel = _resolve_relation_from_env(model_state)
    else:
        rel = _engine._find_relation_by_id(model_state, relation_id)
    if rel is None:
        raise _engine.CliError("relation not found")

    # Determine which app's databag to read.
    # If a unit is specified, it belongs to some app; use that app.
    # Otherwise, default to the local app's databag.
    if unit:
        target_app = unit.split("/")[0] if "/" in unit else unit
        target_unit = unit
    else:
        target_app = local_app
        target_unit = local_unit

    if is_app:
        bucket = _engine._relation_data_bucket(rel, target_app, None)
    else:
        bucket = _engine._relation_data_bucket(rel, target_app, target_unit)

    if key and key != "-":
        _output(bucket.get(key, ""), output_format)
    else:
        _output(dict(bucket), output_format)
    return 0


def _relation_set(
    tool_args: list[str],
    model_state: dict[str, Any],
    local_app: str,
    local_unit: str,
    state: dict[str, Any],
) -> int:
    """relation-set [-r <ref>] [--app] [--file -] (stdin JSON)"""
    is_app = False
    relation_id: int | None = None
    content = ""
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "-r" and i + 1 < len(tool_args):
            _, relation_id = _parse_relation_ref(tool_args[i + 1])
            i += 2
            continue
        if token.startswith("-r"):
            _, relation_id = _parse_relation_ref(token[2:])
            i += 1
            continue
        if token == "--app":
            is_app = True
            i += 1
            continue
        if token == "--file" and i + 1 < len(tool_args):
            # ops always uses --file - (stdin). Read the JSON from stdin.
            content = sys.stdin.read()
            i += 2
            continue
        if token.startswith("--file="):
            file_path = token.split("=", 1)[1]
            content = open(file_path).read()  # noqa: SIM115
            i += 1
            continue
        i += 1

    if relation_id is None:
        rel = _resolve_relation_from_env(model_state)
    else:
        rel = _engine._find_relation_by_id(model_state, relation_id)
    if rel is None:
        raise _engine.CliError("relation not found")

    try:
        data = json.loads(content) if content.strip() else {}
    except json.JSONDecodeError as exc:
        raise _engine.CliError(f"invalid relation-set JSON: {exc}") from None

    if is_app:
        bucket = _engine._relation_data_bucket(rel, local_app, None)
    else:
        bucket = _engine._relation_data_bucket(rel, local_app, local_unit)

    for k, v in data.items():
        if v == "":
            bucket.pop(k, None)
        else:
            bucket[k] = v

    _engine._save_state(state)
    return 0


def _relation_model_get(tool_args: list[str], model_state: dict[str, Any]) -> int:
    """relation-model-get [-r <ref>] [--format=json]"""
    output_format = "json"
    relation_id: int | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--format" and i + 1 < len(tool_args):
            output_format = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token == "-r" and i + 1 < len(tool_args):
            _, relation_id = _parse_relation_ref(tool_args[i + 1])
            i += 2
            continue
        if token.startswith("-r"):
            _, relation_id = _parse_relation_ref(token[2:])
            i += 1
            continue
        i += 1

    if relation_id is None:
        rel = _resolve_relation_from_env(model_state)
    else:
        rel = _engine._find_relation_by_id(model_state, relation_id)
    if rel is None:
        raise _engine.CliError("relation not found")

    _output({"uuid": model_state.get("uuid", "")}, output_format)
    return 0


# ---------------------------------------------------------------------------
# Secret hook tools
# ---------------------------------------------------------------------------


def _secret_add(
    tool_args: list[str],
    model_state: dict[str, Any],
    app_name: str,
    state: dict[str, Any],
) -> int:
    """secret-add [--label <l>] [--owner application] <key>#file=<path> ..."""
    label: str | None = None
    content: dict[str, str] = {}
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--label" and i + 1 < len(tool_args):
            label = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--label="):
            label = token.split("=", 1)[1]
            i += 1
            continue
        if token == "--owner" and i + 1 < len(tool_args):
            # Owner is always the application in jjx; consume and ignore.
            i += 2
            continue
        if token.startswith("--owner="):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        # key#file=path format
        if "#file=" in token:
            key, _, file_path = token.partition("#file=")
            try:
                content[key] = open(file_path).read()  # noqa: SIM115
            except OSError as exc:
                raise _engine.CliError(f"failed to read secret file {file_path}: {exc}") from None
        i += 1

    secret_id = _engine._next_secret_id(model_state)
    secret = {
        "id": secret_id,
        "label": label,
        "owner": app_name,
        "content": content,
        "revision": 1,
        "grants": [],
    }
    _engine._secrets(model_state).append(secret)
    _engine._save_state(state)
    # secret-add prints the secret ID (the URI form) on stdout
    sys.stdout.write(f"{secret_id}\n")
    return 0


def _secret_get(tool_args: list[str], model_state: dict[str, Any]) -> int:
    """secret-get [--format=json] <id> [--label <l>] [--refresh|--peek]"""
    output_format = "json"
    secret_id: str | None = None
    label: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--format" and i + 1 < len(tool_args):
            output_format = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token == "--label" and i + 1 < len(tool_args):
            label = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--label="):
            label = token.split("=", 1)[1]
            i += 1
            continue
        if token in ("--refresh", "--peek"):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if secret_id is None:
            secret_id = token
        i += 1

    secret = None
    if secret_id:
        secret = _engine._find_secret_by_id(model_state, secret_id)
    if secret is None and label:
        secret = _engine._find_secret_by_label(model_state, label)
    if secret is None:
        raise _engine.CliError(f"secret not found: {secret_id or label}")

    _output(dict(secret.get("content", {})), output_format)
    return 0


def _secret_grant(tool_args: list[str], model_state: dict[str, Any], state: dict[str, Any]) -> int:
    """secret-grant --relation <rid> [--unit <unit>] <id>"""
    relation_id: int | None = None
    unit: str | None = None
    secret_id: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--relation" and i + 1 < len(tool_args):
            relation_id = int(tool_args[i + 1])
            i += 2
            continue
        if token.startswith("--relation="):
            relation_id = int(token.split("=", 1)[1])
            i += 1
            continue
        if token == "--unit" and i + 1 < len(tool_args):
            unit = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--unit="):
            unit = token.split("=", 1)[1]
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if secret_id is None:
            secret_id = token
        i += 1

    if secret_id is None:
        raise _engine.CliError("secret-grant requires a secret ID")
    secret = _engine._find_secret_by_id(model_state, secret_id)
    if secret is None:
        raise _engine.CliError(f"secret not found: {secret_id}")

    grants = secret.setdefault("grants", [])
    grant_entry = {"relation_id": relation_id, "unit": unit}
    if grant_entry not in grants:
        grants.append(grant_entry)
    _engine._save_state(state)
    return 0


def _secret_info_get(tool_args: list[str], model_state: dict[str, Any]) -> int:
    """secret-info-get [--format=json] <id> | --label <l>"""
    output_format = "json"
    secret_id: str | None = None
    label: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--format" and i + 1 < len(tool_args):
            output_format = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--format="):
            output_format = token.split("=", 1)[1]
            i += 1
            continue
        if token == "--label" and i + 1 < len(tool_args):
            label = tool_args[i + 1]
            i += 2
            continue
        if token.startswith("--label="):
            label = token.split("=", 1)[1]
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if secret_id is None:
            secret_id = token
        i += 1

    secret = None
    if secret_id:
        secret = _engine._find_secret_by_id(model_state, secret_id)
    if secret is None and label:
        secret = _engine._find_secret_by_label(model_state, label)
    if secret is None:
        raise _engine.CliError(f"secret not found: {secret_id or label}")

    info = {
        "revision": secret.get("revision", 1),
        "label": secret.get("label"),
        "owner": secret.get("owner"),
        "expires": None,
        "rotation": None,
        "rotates": None,
        "description": None,
    }
    _output(info, output_format)
    return 0


def _secret_ids(model_state: dict[str, Any], app_name: str) -> int:
    """secret-ids [--format=json]"""
    result = []
    for secret in _engine._secrets(model_state):
        if secret.get("owner") == app_name:
            result.append(secret["id"])
    sys.stdout.write(json.dumps(result))
    return 0


def _secret_remove(
    tool_args: list[str], model_state: dict[str, Any], state: dict[str, Any]
) -> int:
    """secret-remove <id> [--revision <n>]"""
    secret_id: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--revision" and i + 1 < len(tool_args):
            i += 2
            continue
        if token.startswith("--revision="):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if secret_id is None:
            secret_id = token
        i += 1

    if secret_id is None:
        raise _engine.CliError("secret-remove requires a secret ID")
    secret = _engine._find_secret_by_id(model_state, secret_id)
    if secret is not None:
        _engine._secrets(model_state).remove(secret)
        _engine._save_state(state)
    return 0


def _secret_revoke(
    tool_args: list[str], model_state: dict[str, Any], state: dict[str, Any]
) -> int:
    """secret-revoke [--relation <rid>] [--app <app>] [--unit <unit>] <id>"""
    relation_id: int | None = None
    secret_id: str | None = None
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token == "--relation" and i + 1 < len(tool_args):
            relation_id = int(tool_args[i + 1])
            i += 2
            continue
        if token.startswith("--relation="):
            relation_id = int(token.split("=", 1)[1])
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if secret_id is None:
            secret_id = token
        i += 1

    if secret_id is None:
        raise _engine.CliError("secret-revoke requires a secret ID")
    secret = _engine._find_secret_by_id(model_state, secret_id)
    if secret is not None:
        grants = secret.get("grants", [])
        secret["grants"] = [g for g in grants if g.get("relation_id") != relation_id]
        _engine._save_state(state)
    return 0


def _secret_set(tool_args: list[str], model_state: dict[str, Any], state: dict[str, Any]) -> int:
    """secret-set [--label <l>] <id> [key#file=<path> ...]"""
    secret_id: str | None = None
    content: dict[str, str] = {}
    i = 0
    while i < len(tool_args):
        token = tool_args[i]
        if token.startswith("--label="):
            i += 1
            continue
        if token == "--label" and i + 1 < len(tool_args):
            i += 2
            continue
        if token.startswith("--owner"):
            if "=" in token:
                i += 1
            else:
                i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        if secret_id is None:
            secret_id = token
        elif "#file=" in token:
            key, _, file_path = token.partition("#file=")
            try:
                content[key] = open(file_path).read()  # noqa: SIM115
            except OSError as exc:
                raise _engine.CliError(f"failed to read secret file {file_path}: {exc}") from None
        i += 1

    if secret_id is None:
        raise _engine.CliError("secret-set requires a secret ID")
    secret = _engine._find_secret_by_id(model_state, secret_id)
    if secret is None:
        raise _engine.CliError(f"secret not found: {secret_id}")
    if content:
        secret["content"].update(content)
        secret["revision"] = secret.get("revision", 1) + 1
    _engine._save_state(state)
    return 0
