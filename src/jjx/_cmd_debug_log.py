"""Debug-log command wrapper."""

from __future__ import annotations

import sys

from . import _engine


def debug_log(args: list[str], model: str | None) -> int:
    """Execute the debug-log command."""
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    limit = 0
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                raise _engine.CliError(f"invalid --limit value: {args[i + 1]}") from None
            i += 2
            continue
        i += 1

    lines = [str(x) for x in model_state.get("logs", [])]
    if limit > 0:
        lines = lines[-limit:]
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
    return 0
