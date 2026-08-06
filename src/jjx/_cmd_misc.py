"""Simple Juju commands that don't warrant their own module.

These are commands that jubilant/pytest-jubilant may call during setup,
teardown, or status checks. They're simple enough to implement inline.
"""

from __future__ import annotations

import json
import sys

from . import _engine


def switch(args: list[str]) -> int:
    """Execute the switch command.

    In jjx, there's no persistent "current model" — the model is always
    specified via --model. This command is a no-op that just succeeds.
    """
    # jubilant calls `juju switch <model>` when --juju-switch is passed.
    # We don't need to do anything — the model is already tracked by jubilant.
    return 0


def version(args: list[str]) -> int:
    """Execute the version command."""
    # jubilant calls `juju version --format json --all`
    # Return a minimal version response.
    result = {
        "version": "3.6.0",
        "git-hash": "jjx",
    }
    sys.stdout.write(json.dumps(result))
    return 0


def show_model(args: list[str], model: str | None) -> int:
    """Execute the show-model command."""
    state = _engine._load_state()

    target_model = model
    if args:
        for token in args:
            if not token.startswith("--"):
                target_model = token
                break

    if target_model is None:
        target_model = _engine._require_model_name(state, None)

    model_state = state.get("models", {}).get(target_model)
    if model_state is None:
        raise _engine.CliError(f"ERROR model {target_model} does not exist")

    result = {
        target_model: {
            "name": target_model,
            "type": "caas",
            "controller": "jjx",
            "cloud": "localhost",
            "uuid": model_state.get("uuid", ""),
            "life": "alive",
            "model-status": {
                "current": "available",
                "message": "available",
            },
        }
    }
    sys.stdout.write(json.dumps(result))
    return 0


def models(args: list[str]) -> int:
    """Execute the models command."""
    state = _engine._load_state()
    result = {
        "models": [
            {
                "name": name,
                "type": "caas",
                "controller": "jjx",
                "cloud": "localhost",
            }
            for name in state.get("models", {})
        ],
    }
    sys.stdout.write(json.dumps(result))
    return 0
