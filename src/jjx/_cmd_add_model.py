"""Add-model command wrapper."""

from __future__ import annotations

import uuid

from . import _engine


def add_model(args: list[str]) -> int:
    """Execute the add-model command."""
    filtered = [a for a in args if a != "--no-switch" and not a.startswith("--config")]
    if not filtered:
        raise _engine.CliError("usage: juju add-model <model>")

    model_name = filtered[0]
    state = _engine._load_state()
    models = state.setdefault("models", {})
    if models and model_name not in models:
        raise _engine.CliError("only a single model is supported")
    if model_name in models:
        raise _engine.CliError(f"model {model_name} already exists")

    model_state = {
        "created_at": _engine._now_iso(),
        "uuid": str(uuid.uuid4()),
        "apps": {},
        "logs": [],
    }
    models[model_name] = model_state
    _engine._append_log(model_state, f"model {model_name} created")
    _engine._save_state(state)
    return 0
