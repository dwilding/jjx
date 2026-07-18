"""Remove-application command wrapper."""

from __future__ import annotations

from . import _engine


def remove_application(args: list[str], model: str | None) -> int:
    """Execute the remove-application command."""
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    app_name = ""
    for token in args:
        if token.startswith("--"):
            continue
        app_name = token
        break

    if not app_name:
        raise _engine.CliError("usage: juju remove-application <app>")

    # Kill this app's background pebble-ready process before removing its
    # containers.
    _engine._kill_background_processes(app_name=app_name)

    app_state = model_state.get("apps", {}).pop(app_name, None)
    if app_state is None:
        return 0

    container_name = app_state.get("container_name")
    if container_name:
        _engine._docker_rm(container_name)

    # Remove the charm runner container after the workload container.
    charm_runner_name = app_state.get("charm_runner_name")
    if charm_runner_name:
        _engine._docker_rm(charm_runner_name)

    _engine._append_log(model_state, f"application {app_name} removed")
    _engine._save_state(state)
    return 0
