"""Simple Juju commands that don't warrant their own module.

These are commands that jubilant/pytest-jubilant may call during setup,
teardown, or status checks. They're simple enough to implement inline.
"""

from __future__ import annotations

import json
import sys

from . import _engine
from ._version import juju_version_string


def switch(args: list[str]) -> int:
    """Execute the switch command.

    In jjx, there's no persistent "current model" — the model is always
    specified via --model. This command is a no-op that just succeeds.
    """
    # jubilant calls `juju switch <model>` when --juju-switch is passed.
    # We don't need to do anything — the model is already tracked by jubilant.
    return 0


def set_model_constraints(args: list[str]) -> int:
    """Execute the set-model-constraints command.

    jjx is single-unit with no real scheduling constraints, so this is a
    no-op that just succeeds. pytest-jubilant 2.3.0+ calls it during model
    setup (via jubilant.Juju.model_constraints()).
    """
    return 0


def version(args: list[str]) -> int:
    """Execute the version command."""
    # jubilant calls `juju version --format json --all` and parses the
    # "version" field with Version._from_dict, which requires the
    # major.minor.patch-release-arch form (a bare "4.0.14" is rejected).
    result = {
        "version": juju_version_string(),
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

    # Juju 4 (and jubilant's ModelInfo parser) uses these field names:
    # short-name, model-uuid, model-type, controller-uuid, controller-name,
    # is-controller. The older 3.x names (type, controller, uuid) are no
    # longer parsed. jjx has no real controller, so we use a fixed UUID.
    result = {
        target_model: {
            "name": target_model,
            "short-name": target_model,
            "model-uuid": model_state.get("uuid", ""),
            "model-type": "caas",
            "controller-uuid": "00000000-0000-0000-0000-000000000000",
            "controller-name": "jjx",
            "is-controller": False,
            "cloud": "localhost",
            "life": "alive",
            "status": {
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
    # Juju 4 uses short-name, model-uuid, model-type, controller-uuid,
    # controller-name, is-controller (matching show-model). jubilant does
    # not parse `juju models` output, but we keep the field names consistent
    # with real Juju 4 for anyone reading the raw output.
    result = {
        "models": [
            {
                "name": name,
                "short-name": name,
                "model-uuid": ms.get("uuid", ""),
                "model-type": "caas",
                "controller-uuid": "00000000-0000-0000-0000-000000000000",
                "controller-name": "jjx",
                "is-controller": False,
                "cloud": "localhost",
                "life": "alive",
            }
            for name, ms in state.get("models", {}).items()
        ],
    }
    sys.stdout.write(json.dumps(result))
    return 0
