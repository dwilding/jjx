"""Destroy-model command wrapper."""

from __future__ import annotations

from . import _engine


def destroy_model(args: list[str]) -> int:
    """Execute the destroy-model command."""
    if not args:
        raise _engine.CliError("usage: juju destroy-model <model>")

    model_name = ""
    for token in args:
        if token.startswith("--"):
            continue
        model_name = token
        break

    if not model_name:
        raise _engine.CliError("usage: juju destroy-model <model>")

    state = _engine._load_state()
    model_state = state.get("models", {}).get(model_name)
    if model_state is None:
        return 0

    # Teardown order: postgres → workload → charm runners.
    # Postgres is removed first so charms can clean up gracefully if needed.
    # Charm runners are removed last so they're available until everything
    # else is torn down.
    charm_runner_names: list[str] = []
    workload_names: list[str] = []
    for app_state in model_state.get("apps", {}).values():
        charm_runner_name = app_state.get("charm_runner_name")
        if charm_runner_name:
            charm_runner_names.append(charm_runner_name)
        container_name = app_state.get("container_name")
        if container_name:
            workload_names.append(container_name)

    # Remove postgres containers first (non-charm, non-workload).
    for container_name in _engine._docker_list_model_containers(model_name):
        if container_name.endswith("-charm"):
            continue
        if container_name in workload_names:
            continue
        _engine._docker_rm(container_name)

    # Remove workload containers.
    for container_name in workload_names:
        _engine._docker_rm(container_name)

    # Remove charm runners last.
    for charm_runner_name in charm_runner_names:
        _engine._docker_rm(charm_runner_name)

    # Clean up any stragglers.
    for container_name in _engine._docker_list_model_containers(model_name):
        _engine._docker_rm(container_name)

    _engine._cleanup_model_artifacts()
    return 0
