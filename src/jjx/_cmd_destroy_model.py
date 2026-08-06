"""Destroy-model command wrapper."""

from __future__ import annotations

from . import _engine, _virtual_registry


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

    # Kill background pebble-ready processes for apps in this model only.
    for app_name in model_state.get("apps", {}):
        _engine._kill_background_processes(app_name=app_name)

    # Collect containers grouped by teardown priority. Lower priority = removed
    # first. The order is: COS containers (grafana=10, prometheus=20, loki=30)
    # → postgres (40) → workload (50) → charm runners (60).
    containers_by_priority: list[tuple[int, str]] = []
    for app_state in model_state.get("apps", {}).values():
        charm_runner_name = app_state.get("charm_runner_name")
        if charm_runner_name:
            containers_by_priority.append((60, charm_runner_name))
        container_name = app_state.get("container_name")
        if not container_name:
            continue
        if app_state.get("virtual"):
            virtual_kind = app_state.get("virtual_kind")
            spec = _virtual_registry.get_spec(virtual_kind or "")
            priority = spec.teardown_priority if spec else 50
            containers_by_priority.append((priority, container_name))
        else:
            containers_by_priority.append((50, container_name))

    # Sort by priority (lowest first) and remove.
    containers_by_priority.sort(key=lambda x: x[0])
    removed_names: list[str] = []
    for _priority, container_name in containers_by_priority:
        _engine._docker_rm(container_name)
        removed_names.append(container_name)

    # Clean up any stragglers — containers that exist but weren't tracked in
    # state (e.g. orphaned by a crash). We only look for containers whose name
    # starts with the model prefix but does NOT start with any other model's
    # prefix (model names can share prefixes, e.g. "foo" and "foo-cos").
    all_model_prefixes = {
        _engine._sanitize_container_name(f"{m}-") for m in state.get("models", {})
    }
    this_prefix = _engine._sanitize_container_name(f"{model_name}-")
    already_removed = set(removed_names)
    for container_name in _engine._docker_list_model_containers(model_name):
        if container_name in already_removed:
            continue
        # Skip containers that belong to a different model with a longer
        # matching prefix (e.g. "model-cos-loki" when destroying "model").
        if any(
            container_name.startswith(other_prefix) and other_prefix != this_prefix
            for other_prefix in all_model_prefixes
        ):
            continue
        _engine._docker_rm(container_name)
    # Remove this model from state. If it's the last model, clean up .jjx/
    # entirely (matching the original behavior).
    del state["models"][model_name]
    if not state["models"]:
        _engine._cleanup_model_artifacts()
    else:
        _engine._save_state(state)
    return 0
