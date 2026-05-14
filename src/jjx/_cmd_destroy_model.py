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

    for app_state in model_state.get("apps", {}).values():
        container_name = app_state.get("container_name")
        if container_name:
            _engine._docker_rm(container_name)

    for container_name in _engine._docker_list_model_containers(model_name):
        _engine._docker_rm(container_name)

    _engine._cleanup_model_artifacts()
    return 0
