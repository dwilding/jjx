"""Deploy command wrapper.

This module is kept intentionally small so command-specific logic lives in one
place, while external imports of ``jjx._cmd_deploy`` remain stable.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from . import _engine, _virtual_postgres


# Charm names that jjx handles as "virtual" charms — no real charm code is
# run; instead jjx manages the workload and relation data directly.
_VIRTUAL_CHARMS = {
    "postgresql-k8s": "postgresql",
}


_DOCKER_PUBLISH_RE = re.compile(r"^(?P<host_port>\d{1,5}):(?P<container_port>\d{1,5})$")


def _parse_docker_publish(raw: str) -> str:
    match = _DOCKER_PUBLISH_RE.fullmatch(raw)
    if not match:
        raise _engine.CliError("JJX_DOCKER_PUBLISH must be in HOST_PORT:CONTAINER_PORT format")

    host_port = int(match.group("host_port"))
    container_port = int(match.group("container_port"))
    if not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
        raise _engine.CliError("JJX_DOCKER_PUBLISH ports must be between 1 and 65535")

    return f"127.0.0.1:{host_port}:{container_port}"


def _docker_publish_from_env() -> str | None:
    raw = os.environ.get("JJX_DOCKER_PUBLISH", "").strip()
    if not raw:
        return None
    return _parse_docker_publish(raw)


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
        # Default the app name to the charm name (like Juju does).
        app_name = charm_path
    return charm_path, app_name, resources


def _workload_spec(charm_source: Path) -> tuple[str, str]:
    data = _engine._read_yaml(charm_source / "charmcraft.yaml")
    containers = data.get("containers", {})
    if isinstance(containers, dict) and containers:
        first_name = next(iter(containers.keys()))
        first_spec = containers[first_name]
        if not isinstance(first_name, str) or not first_name:
            raise _engine.CliError("charm metadata has an invalid container name")
        if not isinstance(first_spec, dict):
            raise _engine.CliError(f"container {first_name} metadata must be a mapping")
        resource_name = first_spec.get("resource")
        if not isinstance(resource_name, str) or not resource_name:
            raise _engine.CliError(f"container {first_name} must define a resource")
        return first_name, resource_name
    raise _engine.CliError("charm must define at least one workload container")


def deploy(args: list[str], model: str | None) -> int:
    """Execute the deploy command."""
    if not args:
        raise _engine.CliError("usage: juju deploy <charm> <app> [--resource name=image]")

    state = _engine._load_state()
    if model is None and not state.get("models"):
        model = "jjx-default"
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
    existing = model_state["apps"].get(app_name)
    if existing and existing.get("container_name"):
        _engine._docker_rm(existing["container_name"])

    # Handle virtual charms (e.g. postgresql-k8s) — no charm code, no Pebble.
    virtual_kind = _VIRTUAL_CHARMS.get(charm_path)
    if virtual_kind is None and app_name:
        virtual_kind = _VIRTUAL_CHARMS.get(app_name)
    if virtual_kind is not None:
        return _deploy_virtual(state, model_name, app_name, virtual_kind)

    charm_source = _engine._discover_charm_source(charm_path, app_name)
    workload, resource_name = _workload_spec(charm_source)
    image = resources.get(resource_name)
    if not image:
        raise _engine.CliError(f"missing required --resource {resource_name}=<image>")

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

    python_exe = _engine._python_executable()
    _engine._ensure_hook_tools(python_exe)

    socket_path = _engine._socket_path()
    if socket_path.exists() or socket_path.is_symlink():
        socket_path.unlink()

    pebble_binary = _engine._resolve_pebble_binary()
    if not pebble_binary.is_file():
        raise _engine.CliError(f"pebble cache path is not a file: {pebble_binary}")

    mounts = [
        (str(pebble_binary), "/charm/bin/pebble", True),
        (str(jjx_dir), "/jjx", False),
    ]
    publish = _docker_publish_from_env()
    container_id = _engine._docker_run(
        image,
        container_name,
        mounts=mounts,
        tmpfs_mounts=["/plan:mode=1777", "/var/lib/pebble/default:mode=1777"],
        publish=publish,
        env={
            "PEBBLE": "/var/lib/pebble/default",
            "PEBBLE_SOCKET": "/jjx/socket",
            "PYTHONPATH": "/",
        },
        user=f"{os.getuid()}:{os.getgid()}",
        workdir="/plan",
        entrypoint="/charm/bin/pebble",
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


def _deploy_virtual(
    state: dict[str, Any],
    model_name: str,
    app_name: str,
    virtual_kind: str,
) -> int:
    """Deploy a virtual charm (no charm code, no Pebble).

    For postgresql, this starts a real PostgreSQL container and records the
    app as active in state. The relation data is populated later when
    ``juju integrate`` is called.
    """
    model_state = state["models"][model_name]

    if virtual_kind == "postgresql":
        # The database name is fixed for now; the charm requests "names_db".
        # We'll read it from the relation when integrate is called, but we
        # need a default DB to create at deploy time.
        database_name = "names_db"
        pg_info = _virtual_postgres.start_postgres(model_name, app_name, database_name)
        model_state["apps"][app_name] = {
            "charm": app_name,
            "charm_source": "",
            "virtual": True,
            "virtual_kind": virtual_kind,
            "resources": {},
            "config": {},
            "container_name": pg_info["container_name"],
            "container_id": pg_info["container_id"],
            "unit": f"{app_name}/0",
            "workload": "",
            "pg_info": pg_info,
            "unit_status": _engine._status_dict("active", ""),
            "app_status": _engine._status_dict("active", ""),
            "updated_at": _engine._now_iso(),
        }
        _engine._append_log(
            model_state, f"virtual {app_name} deployed (postgres at {pg_info['ip_address']})"
        )
    else:
        raise _engine.CliError(f"unknown virtual charm kind: {virtual_kind}")

    _engine._save_state(state)
    return 0
