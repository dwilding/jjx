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

    app_state = model_state.get("apps", {}).pop(app_name, None)
    if app_state is None:
        return 0

    container_name = app_state.get("container_name")
    if container_name:
        _engine._docker_rm(container_name)

    _engine._append_log(model_state, f"application {app_name} removed")
    _engine._save_state(state)
    return 0
