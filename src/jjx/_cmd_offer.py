"""Offer command wrapper.

Records a cross-model offer for an application's endpoint. Offers are stored
in model state and resolved when ``juju integrate`` is called with a
cross-model reference like ``admin/<model>.<app>``.
"""

from __future__ import annotations

from . import _engine


def offer(args: list[str], model: str | None) -> int:
    """Execute the offer command.

    Usage: juju offer <app>:<endpoint> [name]
    or:    juju offer <model>.<app>:<endpoint> [name]
    """
    if not args:
        raise _engine.CliError("usage: juju offer <app>:<endpoint> [name]")

    # Parse the app:endpoint spec. It may include a model prefix:
    # "model.app:endpoint" or just "app:endpoint".
    spec = args[0]
    offer_name = None
    if len(args) > 1:
        offer_name = args[1]

    # Parse "model.app:endpoint" or "app:endpoint"
    if ":" in spec:
        app_part, _, endpoint = spec.partition(":")
    else:
        raise _engine.CliError(f"usage: juju offer <app>:<endpoint> (got: {spec})")

    # Check for model prefix: "model.app"
    if "." in app_part:
        model_name, _, app_name = app_part.partition(".")
    else:
        model_name = None  # Use the current model
        app_name = app_part

    state = _engine._load_state()

    # Resolve the model if not specified in the spec.
    if model_name is None:
        model_name = _engine._require_model_name(state, model)

    model_state = state["models"].get(model_name)
    if model_state is None:
        raise _engine.CliError(f"ERROR model {model_name} does not exist")

    # Verify the app exists.
    apps = model_state.get("apps", {})
    if app_name not in apps:
        raise _engine.CliError(f"ERROR application {app_name} not found")

    # Default offer name is the app name.
    if offer_name is None:
        offer_name = app_name

    # Record the offer in model state.
    offers = model_state.setdefault("offers", [])
    # Remove any existing offer with the same name.
    offers[:] = [o for o in offers if o.get("name") != offer_name]
    offers.append(
        {
            "name": offer_name,
            "app": app_name,
            "endpoint": endpoint,
            "model": model_name,
        }
    )
    _engine._append_log(model_state, f"offer {offer_name} created: {app_name}:{endpoint}")
    _engine._save_state(state)
    return 0
