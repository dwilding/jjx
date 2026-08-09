"""Deploy command wrapper.

This module is kept intentionally small so command-specific logic lives in one
place, while external imports of ``jjx._cmd_deploy`` remain stable.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from . import (
    _engine,
    _virtual_bundle,
    _virtual_registry,
)

# Charm names that jjx handles as "virtual" charms — no real charm code is
# run; instead jjx manages the workload and relation data directly.
_VIRTUAL_CHARMS = {
    "postgresql-k8s": "postgresql",
    "loki-k8s": "loki",
    "prometheus-k8s": "prometheus",
    "grafana-k8s": "grafana",
}


_PUBLISH_RE = re.compile(r"^(?P<host_port>\d{1,5}):(?P<container_port>\d{1,5})$")


def _parse_publish(raw: str) -> str:
    match = _PUBLISH_RE.fullmatch(raw)
    if not match:
        raise _engine.CliError("JJX_PUBLISH must be in HOST_PORT:CONTAINER_PORT format")

    host_port = int(match.group("host_port"))
    container_port = int(match.group("container_port"))
    if not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
        raise _engine.CliError("JJX_PUBLISH ports must be between 1 and 65535")

    return f"127.0.0.1:{host_port}:{container_port}"


def _publish_from_env() -> str | None:
    raw = os.environ.get("JJX_PUBLISH", "").strip()
    if not raw:
        return None
    return _parse_publish(raw)


def _copy_image_pebble_layers(image: str, dest_layers_dir: Path) -> None:
    """Copy baked-in Pebble layers from an OCI image into the state directory.

    Some OCI images (e.g. rocks built with Rockcraft) ship Pebble layers at
    ``/var/lib/pebble/default/layers/``. These define service defaults like
    ``startup: enabled`` that charm layers using ``override: merge`` inherit.

    Since jjx uses a separate Pebble state path (not the image's
    ``/var/lib/pebble/default``), we copy these layers into our state directory
    so Pebble sees them. If the image has no layers, this is a no-op.
    """
    # Use a throwaway container to extract layers from the image filesystem.
    # We can't use `docker cp` from a running container because the image may
    # not have a shell or any entrypoint that stays alive.
    container_name = f"jjx-layer-copy-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(
            [_engine._CONTAINER_BINARY, "create", "--name", container_name, image, "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Copy the layers directory out of the image.
        # Non-zero exit just means the image has no layers directory — that's fine.
        subprocess.run(
            [
                _engine._CONTAINER_BINARY,
                "cp",
                f"{container_name}:/var/lib/pebble/default/layers/.",
                str(dest_layers_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(
            [_engine._CONTAINER_BINARY, "rm", "--force", container_name],
            capture_output=True,
            text=True,
            check=False,
        )


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

    # Handle virtual bundles (e.g. cos-lite) — deploy multiple virtual charms.
    if _virtual_bundle.is_virtual_bundle(charm_path):
        return _virtual_bundle.deploy_bundle(state, model_name, charm_path)

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

    # Name the workload container after the container name (from charmcraft.yaml),
    # not the app name — matching real Juju, where the pod/container is named
    # after the workload container, not the application.
    container_name = _engine._sanitize_container_name(f"{model_name}-{workload}")
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

    socket_path = jjx_dir / "socket"
    if socket_path.exists() or socket_path.is_symlink():
        socket_path.unlink()

    pebble_binary = _engine._resolve_pebble_binary()
    if not pebble_binary.is_file():
        raise _engine.CliError(f"pebble cache path is not a file: {pebble_binary}")

    # Prepare Pebble state directory on the host (bind-mounted at /jjx).
    # We use /jjx/pebble as the Pebble state path (PEBBLE env) instead of
    # the image's /var/lib/pebble/default. This avoids shadowing the image's
    # baked-in Pebble layers with a tmpfs mount. Instead, we copy any layers
    # from the image into our state directory so they are visible to Pebble.
    pebble_state_dir = jjx_dir / "pebble"
    pebble_layers_dir = pebble_state_dir / "layers"
    pebble_layers_dir.mkdir(parents=True, exist_ok=True)

    # Copy baked-in Pebble layers from the OCI image (if any) into our state
    # directory. This preserves the image's service definitions (e.g. startup:
    # enabled) so that charm layers using override: merge inherit them correctly.
    _copy_image_pebble_layers(image, pebble_layers_dir)

    mounts = [
        (str(pebble_binary), "/charm/bin/pebble", True),
        (str(jjx_dir), "/jjx", False),
    ]
    publish = _publish_from_env()
    container_id = _engine._docker_run(
        image,
        container_name,
        mounts=mounts,
        tmpfs_mounts=["/plan:mode=1777"],
        publish=publish,
        env={
            "PEBBLE": "/jjx/pebble",
            "PEBBLE_SOCKET": "/jjx/socket",
            "PYTHONPATH": "/",
        },
        user=_engine._container_user(),
        network="bridge",
        workdir="/plan",
        entrypoint="/charm/bin/pebble",
        command=["run", "--hold", "--create-dirs"],
    )
    model_state["apps"][app_name]["container_id"] = container_id
    # Store the workload container's IP address in state so hook tools
    # (e.g. network-get) can access it without calling docker directly,
    # which isn't available inside the charm runner container.
    try:
        container_ip = _engine._docker_container_ip(container_name)
    except _engine.CliError:
        container_ip = ""
    model_state["apps"][app_name]["container_ip"] = container_ip

    # Start the charm runner container — a persistent container that shares
    # the workload's network namespace and runs charm hooks via docker exec.
    _engine._ensure_charm_runner_image()
    _engine._start_charm_runner(model_name, container_name, model_state["apps"][app_name])
    _engine._save_state(state)

    charm_runner_name = model_state["apps"][app_name].get("charm_runner_name", "")
    container_python = model_state["apps"][app_name].get("container_python", "")
    if charm_runner_name and container_python:
        _engine._wait_for_charm_runner_socket(charm_runner_name, container_python)

    _engine._append_log(model_state, f"application {app_name} deployed with image {image}")
    _engine._save_state(state)

    try:
        # Run config-changed synchronously so the charm can set status before
        # the test's wait() starts polling. Pebble-ready is dispatched
        # asynchronously after a minimum delay (see _spawn_background_pebble_ready).
        _engine._run_config_changed_event(model_name, app_name)
        _engine._spawn_background_pebble_ready(model_name, app_name, workload)
    except Exception:
        state = _engine._load_state()
        app_state = state.get("models", {}).get(model_name, {}).get("apps", {}).get(app_name)
        if app_state:
            # Teardown order: workload → charm runner.
            if app_state.get("container_name"):
                _engine._docker_rm(app_state["container_name"])
            if app_state.get("charm_runner_name"):
                _engine._docker_rm(app_state["charm_runner_name"])
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

    Uses the virtual charm registry to start the workload and create the
    app state. The relation data is populated later when ``juju integrate``
    is called.
    """
    spec = _virtual_registry.get_spec(virtual_kind)
    if spec is None:
        raise _engine.CliError(f"unknown virtual charm kind: {virtual_kind}")

    model_state = state["models"][model_name]
    info = spec.start(model_name, app_name)
    model_state["apps"][app_name] = _virtual_registry.make_app_state(virtual_kind, app_name, info)
    _engine._append_log(
        model_state,
        f"virtual {app_name} deployed ({virtual_kind} at {info.get('ip_address', 'no IP')})",
    )
    _engine._save_state(state)
    return 0
