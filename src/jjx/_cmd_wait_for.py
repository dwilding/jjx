"""Wait-for command wrapper."""

from __future__ import annotations

import time

from . import _engine


def _parse_timeout(raw: str | None) -> float:
    if not raw:
        return 300.0
    txt = raw.strip()
    if txt.endswith("s"):
        return float(txt[:-1])
    return float(txt)


def wait_for(args: list[str], model: str | None) -> int:
    """Execute the wait-for command."""
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)

    if len(args) < 2 or args[0] != "application":
        raise _engine.CliError("usage: juju wait-for application <app> [--timeout 60s]")

    app_name = args[1]
    timeout_arg: str | None = None

    i = 2
    while i < len(args):
        if args[i] == "--timeout" and i + 1 < len(args):
            timeout_arg = args[i + 1]
            i += 2
            continue
        i += 1

    deadline = time.monotonic() + _parse_timeout(timeout_arg)
    while time.monotonic() < deadline:
        state = _engine._load_state()
        app_state = state.get("models", {}).get(model_name, {}).get("apps", {}).get(app_name)
        if app_state:
            status = (app_state.get("app_status") or {}).get("status")
            if status in {"active", "blocked"}:
                return 0
        time.sleep(0.25)

    raise _engine.CliError(f"timed out waiting for application {app_name}")
