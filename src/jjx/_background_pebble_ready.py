"""Background pebble-ready dispatcher, spawned by deploy().

This runs as a detached subprocess. It waits for the minimum deploy delay,
then dispatches the pebble-ready event. It does not hold any lock; state
writes are made atomic by _save_state.
"""

from __future__ import annotations

import os
import sys
import time

from . import _engine


def main() -> int:
    state_dir = sys.argv[1]
    model_name = sys.argv[2]
    app_name = sys.argv[3]
    workload_name = sys.argv[4]

    os.environ["JJX_STATE_DIR"] = state_dir

    deadline = time.monotonic() + _engine.PEBBLE_READY_DELAY
    while time.monotonic() < deadline:
        time.sleep(min(0.5, deadline - time.monotonic()))

    try:
        _engine._run_pebble_ready_event(model_name, app_name, workload_name)
    except Exception:
        # _run_charm_event has already set error status in state.
        return 1

    # Clean up the PID marker file.
    marker = _engine._jjx_dir() / f"{app_name}.{os.getpid()}.deploy"
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
