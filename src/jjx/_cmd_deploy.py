"""Deploy command wrapper.

This module is kept intentionally small so command-specific logic lives in one
place, while external imports of ``jjx._cmd_deploy`` remain stable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import _engine


def _parse_deploy_args(args: list[str]) -> tuple[str, str, dict[str, str]]:
    if not args:
        raise _engine.CliError("usage: juju deploy <charm> <app> [--resource name=image]")

    charm_path = args[0]
    resources: dict[str, str] = {}
    app_name: str | None = None

    i = 1
    while i < len(args):
        token = args[i]
        if token == "--resource":
            if i + 1 >= len(args):
                raise _engine.CliError("option --resource needs an argument")
            key, value = _engine._split_resource(args[i + 1])
            resources[key] = value
            i += 2
            continue
        if token.startswith("--resource="):
            key, value = _engine._split_resource(token.split("=", 1)[1])
            resources[key] = value
            i += 1
            continue
        if token.startswith("--"):
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                i += 2
            else:
                i += 1
            continue
        if app_name is None:
            app_name = token
        i += 1

    if app_name is None:
        raise _engine.CliError("deploy requires an application name")
    return charm_path, app_name, resources


def _workload_name(charm_source: Path) -> str:
    data = _engine._read_yaml(charm_source / "charmcraft.yaml")
    containers = data.get("containers", {})
    if isinstance(containers, dict) and containers:
        first_name = next(iter(containers.keys()))
        if isinstance(first_name, str):
            return first_name
    return "httpbin"


def deploy(args: list[str], model: str | None) -> int:
    """Execute the deploy command."""
    if not args:
        raise _engine.CliError("usage: juju deploy <charm> <app> [--resource name=image]")

    state = _engine._load_state()
    if model is None and not state.get("models"):
        model = "default"
        state.setdefault("models", {})[model] = {
            "created_at": _engine._now_iso(),
            "uuid": str(uuid.uuid4()),
            "apps": {},
            "logs": [],
        }
        _engine._save_state(state)

    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    charm_path, app_name, resources = _parse_deploy_args(args)
    image = resources.get("httpbin-image")
    if not image:
        raise _engine.CliError("missing required --resource httpbin-image=<image>")

    existing = model_state["apps"].get(app_name)
    if existing and existing.get("container_name"):
        _engine._docker_rm(existing["container_name"])

    charm_source = _engine._discover_charm_source(charm_path, app_name)
    workload = _workload_name(charm_source)
    container_name = _engine._sanitize_container_name(f"{model_name}-{app_name}")
    defaults = _engine._default_config(charm_source)

    model_state["apps"][app_name] = {
        "charm": charm_path,
        "charm_source": str(charm_source),
        "resources": resources,
        "config": defaults,
        "container_name": container_name,
        "container_id": "",
        "unit": f"{app_name}/0",
        "workload": workload,
        "unit_status": _engine._status_dict("maintenance", "deploying"),
        "app_status": _engine._status_dict("maintenance", "deploying"),
        "updated_at": _engine._now_iso(),
    }
    _engine._ensure_runtime_layout(model_state["apps"][app_name])

    jjx_dir = _engine._jjx_dir()
    jjx_dir.mkdir(parents=True, exist_ok=True)
    pebble_dir = _engine._pebble_dir()
    pebble_dir.mkdir(parents=True, exist_ok=True)

    python_exe = _engine._python_executable()
    _engine._ensure_hook_tools(python_exe)

    socket_path = _engine._socket_path()
    if socket_path.exists() or socket_path.is_symlink():
        socket_path.unlink()

    pebble_binary = _engine._resolve_pebble_binary()
    if not pebble_binary.is_file():
        raise _engine.CliError(f"pebble cache path is not a file: {pebble_binary}")
    mounted_pebble_binary = _engine._staged_pebble_binary(pebble_binary)

    mounts = [
        (str(mounted_pebble_binary), "/tmp/jjx-pebble", True),
        (str(jjx_dir), "/jjx", False),
    ]
    container_id = _engine._docker_run(
        image,
        container_name,
        mounts=mounts,
        env={
            "PEBBLE": "/jjx/pebble",
            "PEBBLE_SOCKET": "/jjx/socket",
        },
        user=f"{os.getuid()}:{os.getgid()}",
        entrypoint="/tmp/jjx-pebble",
        command=["run", "--create-dirs"],
    )
    model_state["apps"][app_name]["container_id"] = container_id

    _engine._append_log(model_state, f"application {app_name} deployed with image {image}")
    _engine._save_state(state)

    try:
        _engine._run_deploy_event_flow(model_name, app_name, workload)
    except Exception:
        state = _engine._load_state()
        app_state = state.get("models", {}).get(model_name, {}).get("apps", {}).get(app_name)
        if app_state and app_state.get("container_name"):
            _engine._docker_rm(app_state["container_name"])
        raise

    state = _engine._load_state()
    state["models"][model_name]["apps"][app_name]["updated_at"] = _engine._now_iso()
    _engine._save_state(state)
    return 0
