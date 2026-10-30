"""Entrypoints for the `jjx` CLI and the `juju` compatibility CLI."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from types import FrameType

from . import (
    _cmd_add_model,
    _cmd_config,
    _cmd_debug_log,
    _cmd_deploy,
    _cmd_destroy_model,
    _cmd_hook_tool,
    _cmd_integrate,
    _cmd_misc,
    _cmd_offer,
    _cmd_remove_application,
    _cmd_run,
    _cmd_secret,
    _cmd_status,
    _cmd_wait_for,
    _engine,
)


def extract_model(args: list[str]) -> tuple[str | None, list[str]]:
    if "--model" not in args:
        return None, args

    idx = args.index("--model")
    if idx + 1 >= len(args):
        raise _engine.CliError("ERROR option --model needs an argument")
    model = args[idx + 1]
    stripped = args[:idx] + args[idx + 2 :]
    return model, stripped


def run_juju_command(argv: list[str]) -> int:
    if not argv:
        raise _engine.CliError("usage: juju <command> [options]")

    command = argv[0]
    if command == "_hook-tool":
        return _cmd_hook_tool.hook_tool(argv[1:])

    model, rest = extract_model(argv[1:])

    if command == "add-model":
        return _cmd_add_model.add_model(rest)
    if command == "deploy":
        return _cmd_deploy.deploy(rest, model)
    if command == "integrate":
        return _cmd_integrate.integrate(rest, model)
    if command == "remove-application":
        return _cmd_remove_application.remove_application(rest, model)
    if command == "config":
        return _cmd_config.config(rest, model)
    if command == "status":
        return _cmd_status.status(rest, model)
    if command == "wait-for":
        return _cmd_wait_for.wait_for(rest, model)
    if command == "debug-log":
        return _cmd_debug_log.debug_log(rest, model)
    if command == "destroy-model":
        return _cmd_destroy_model.destroy_model(rest)
    if command == "offer":
        return _cmd_offer.offer(rest, model)
    if command == "run":
        return _cmd_run.run(rest, model)
    if command == "switch":
        return _cmd_misc.switch(rest)
    if command == "set-model-constraints":
        return _cmd_misc.set_model_constraints(rest)
    if command == "version":
        return _cmd_misc.version(rest)
    if command == "show-model":
        return _cmd_misc.show_model(rest, model)
    if command == "models":
        return _cmd_misc.models(rest)
    if command == "add-secret":
        return _cmd_secret.add_secret(rest, model)
    if command == "grant-secret":
        return _cmd_secret.grant_secret(rest, model)
    if command == "update-secret":
        return _cmd_secret.update_secret(rest, model)
    if command == "secrets":
        return _cmd_secret.secrets(rest, model)
    if command == "show-secret":
        return _cmd_secret.show_secret(rest, model)
    if command == "remove-secret":
        return _cmd_secret.remove_secret(rest, model)

    raise _engine.CliError(f"unknown command: {command}")


def juju_dispatch(argv: list[str]) -> int:
    """Run a Juju-compatible argv vector and return an exit code."""
    try:
        return run_juju_command(argv)
    except _engine.CliError as exc:
        if exc.message:
            sys.stderr.write(exc.message + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        # Ctrl-C tears the jjx session down, and the relayed SIGINT reaches any
        # in-flight `juju` shim call. Exit quietly with the standard code.
        return 130  # Standard exit code for SIGINT


def run_hook_tool(tool: str, args: list[str]) -> int:
    """Run one internal hook tool with the given arguments."""
    return juju_dispatch(["_hook-tool", tool, *args])


def juju_cli() -> int:
    """Run the `juju` compatibility CLI and return an exit code.

    The `juju` CLI is intended to be run when the `jjx` package is installed in the charm's venv.
    """
    return juju_dispatch(sys.argv[1:])


def teardown_all_models() -> None:
    """Destroy all models currently in state."""
    state = _engine._load_state()
    # Destroy models in reverse creation order so that COS models (created
    # later by JujuFactory) are torn down before the charm's model. This
    # produces a more natural teardown order: COS containers first.
    for model_name in reversed(list(state.get("models", {}))):
        _cmd_destroy_model.destroy_model([model_name])
    # Sweep for orphaned jjx-layer-copy-* helper containers. These are created
    # by deploy to extract Pebble layers and removed in a finally block, but a
    # Ctrl-C mid-deploy can leave them behind. They aren't tracked in state.
    _engine._remove_layer_copy_containers()


def _cos_endpoint_lines() -> list[str]:
    """Return human-readable endpoint lines for virtual COS charms across all models.

    Uses the virtual charm registry to find charms with display names.
    Returns lines like ``Loki         http://172.17.0.3:3100`` so the user can
    interact with them directly in a browser or via curl.
    """
    from . import _virtual_registry

    state = _engine._load_state()
    endpoints: dict[str, str] = {}
    for model_state in state.get("models", {}).values():
        for app_state in model_state.get("apps", {}).values():
            if not app_state.get("virtual"):
                continue
            virtual_kind = app_state.get("virtual_kind")
            spec = _virtual_registry.get_spec(virtual_kind or "")
            if spec is None or spec.display_name is None:
                continue
            info = app_state.get(spec.info_key, {})
            url = _virtual_registry.resolve_endpoint_url(info, spec.default_port)
            if url:
                endpoints[spec.display_name] = url

    # Return sorted by teardown_priority (grafana first, then prometheus, loki)
    specs_with_endpoints = [
        (s, endpoints[s.display_name])
        for s in _virtual_registry._REGISTRY.values()
        if s.display_name and s.display_name in endpoints
    ]
    specs_with_endpoints.sort(key=lambda x: x[0].teardown_priority)
    return [f"{spec.display_name:<12} {url}" for spec, url in specs_with_endpoints]


def jjx_pytest_env_args(charm_root: Path) -> list[str]:
    """Return uv-run args that keep jjx resolution consistent with launch mode.

    Three launch modes are supported:

    1. **Charm venv** — the user added ``jjx`` to their charm's dependencies and
       runs ``uv run jjx``.  The ``juju`` shim lives in the charm's ``.venv/bin``
       with a shebang pointing at the charm venv Python.  Pin uv to that
       interpreter so the inner ``uv run`` reuses the same venv.

    2. **Local checkout** — the developer runs ``uvx --with-editable <repo> jjx``
       (or ``uv tool install <repo>`` from a local path).  The ``jjx`` package
       is importable from a directory whose parent contains ``pyproject.toml``.
       Use ``--with-editable`` so the inner ``uv run`` installs the same source.

    3. **Tool install** — the user ran ``uv tool install jjx`` from PyPI (or
       ``git+<url>``).  The ``juju`` shim is on ``PATH`` with a hardcoded shebang
       pointing at the tool's own Python, which already has the correct ``jjx``.
       No injection is needed — the inner ``uv run`` only needs the charm's
       integration dependencies.
    """
    charm_venv_dir = (charm_root / ".venv").absolute()
    current_python = Path(sys.executable).absolute()

    # Case 1: running from the charm venv; pin uv to the current interpreter.
    if charm_venv_dir in current_python.parents:
        return ["--python", sys.executable]

    package_root = Path(__file__).resolve().parents[2]

    # Case 2: running from a local checkout; use editable source.
    if (package_root / "pyproject.toml").exists():
        return ["--with-editable", str(package_root)]

    # Case 3: installed as a tool (PyPI or git).  The `juju` shim is already on
    # PATH with a shebang pointing at a Python that has jjx — no injection needed.
    return []


def _split_cli_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv into jjx options and extra pytest args at the ``--`` separator.

    Everything before ``--`` is jjx's own flags (``-d``, ``-p``, ``down``).
    Everything after ``--`` is passed through as extra pytest arguments.
    """
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1 :]
    return argv, []


def jjx_pytest_args(charm_root: Path, cli_extra_args: list[str] | None = None) -> list[str]:
    """Return pytest args: built-in defaults, pyproject extra-args, then CLI extra-args.

    CLI extra-args (passed after ``--`` on the command line) are appended last
    so they take precedence over ``[tool.jjx].pytest-extra-args``, matching the
    convention that command-line options override configuration file defaults.
    """
    default_args = ["tests/integration", "--no-juju-teardown", "-v"]
    cli_args = cli_extra_args or []
    pyproject = charm_root / "pyproject.toml"
    if not pyproject.exists():
        return [*default_args, *cli_args]

    try:
        with pyproject.open("rb") as fp:
            config = tomllib.load(fp)
    except tomllib.TOMLDecodeError as exc:
        raise _engine.CliError(f"ERROR: Invalid pyproject.toml: {exc}") from exc

    extra_args = config.get("tool", {}).get("jjx", {}).get("pytest-extra-args")
    if extra_args is None:
        return [*default_args, *cli_args]

    if not isinstance(extra_args, list) or not all(isinstance(arg, str) for arg in extra_args):
        raise _engine.CliError("ERROR: [tool.jjx].pytest-extra-args must be an array of strings")

    return [*default_args, *extra_args, *cli_args]


def _has_pytest_jubilant(charm_root: Path) -> bool:
    """Check whether pytest-jubilant is available in the charm's integration group."""
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "integration",
            "--quiet",
            "python",
            "-c",
            "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('pytest_jubilant') else 1)",
        ],
        cwd=charm_root,
        env=env,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


_INTERRUPT_GRACE_SECONDS = 60.0


def _group_alive(pgid: int) -> bool:
    """Return whether any process still exists in the given process group."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _killpg_safe(pgid: int, sig: int) -> None:
    """Send a signal to a process group, tolerating its death between calls."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _wait_group_gone(pgid: int, received: list[tuple[int, float]], grace: float) -> None:
    """Block until no processes remain in the group, SIGKILLing after the grace period.

    A further Ctrl-C arriving more than a quarter-second after the first also
    escalates to SIGKILL. Launchers such as uvx relay their own duplicate copy
    of each SIGINT jjx receives; those land within milliseconds and must not
    count as a second press. Once SIGKILLed, a few more seconds are allowed
    for stragglers stuck in uninterruptible sleep, then the wait is abandoned
    so teardown can still proceed.
    """
    first_time = received[0][1]
    deadline = first_time + grace
    while _group_alive(pgid):
        last_time = received[-1][1]
        if last_time - first_time > 0.25 or time.monotonic() >= deadline:
            _killpg_safe(pgid, signal.SIGKILL)
        if time.monotonic() >= deadline + 5:
            break
        time.sleep(0.2)


def _pause_until_signalled() -> None:
    """Sit at the 'Press Ctrl-C' prompt until SIGINT or SIGHUP arrives.

    Never raises: just like in the test phase, launchers such as uvx relay a
    duplicate of the interrupt back to jjx within milliseconds, and that
    must not tear the teardown logic of the caller apart with an unexpected
    KeyboardInterrupt. If teardown is interrupted deliberately with a second
    press a quarter-second or more later, SIGINT propagates normally again
    once the caller's exception handlers are gone; the standard handler is
    restored before returning.
    """
    received: list[tuple[int, float]] = []

    def count(signum: int, frame: FrameType | None) -> None:
        received.append((signum, time.monotonic()))

    priors = [(sig, signal.signal(sig, count)) for sig in (signal.SIGINT, signal.SIGHUP)]
    try:
        while not received:
            signal.pause()
    finally:
        for sig, prior in priors:
            signal.signal(sig, prior)


def _run_pytest_session(cmd: list[str], env: dict[str, str]) -> tuple[int, bool]:
    """Run pytest in its own session and relay interrupts, waiting for exit.

    Returns ``(returncode, interrupted)``. The test tree runs in its own
    session so a terminal Ctrl-C reaches jjx only; jjx decides what reaches
    pytest. On SIGINT it kills `uv` (which would relay a second SIGINT and
    abort pytest's graceful shutdown) and forwards one SIGINT to the tree,
    then waits for the tree to finish before returning, so model teardown
    cannot race the debug-log dump pytest-jubilant does at the end of a
    failing session. SIGHUP instead SIGKILLs the tree: with the terminal
    gone there is no output left to preserve.
    """
    received: list[tuple[int, float]] = []

    def count(signum: int, frame: FrameType | None) -> None:
        received.append((signum, time.monotonic()))

    # Never raises: uvx relays a duplicate of jjx's SIGINT back to it. Installed
    # before Popen so there is no window where an interrupt escapes handling.
    priors = [(sig, signal.signal(sig, count)) for sig in (signal.SIGINT, signal.SIGHUP)]

    try:
        proc = subprocess.Popen(cmd, env=env, start_new_session=True)
        # start_new_session: the child leads its own session and group, so its
        # pgid is its pid and terminal Ctrl-C never reaches the test tree.
        pgid = proc.pid
    except BaseException:
        for sig, prior in priors:
            signal.signal(sig, prior)
        raise

    try:
        while True:
            try:
                returncode = proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if not received:
                    continue
                if received[0][0] != signal.SIGHUP:
                    print(flush=True)  # move off the terminal's "^C" line
                proc.kill()  # then reap: a zombie uv keeps the group alive
                proc.wait()
                if received[0][0] == signal.SIGHUP:
                    _killpg_safe(pgid, signal.SIGKILL)
                    _wait_group_gone(pgid, received, grace=1.0)
                else:
                    _killpg_safe(pgid, signal.SIGINT)
                    _wait_group_gone(pgid, received, _INTERRUPT_GRACE_SECONDS)
                returncode = proc.returncode
                break
        if received:
            returncode = 130
        return returncode, bool(received)
    finally:
        for sig, prior in priors:
            signal.signal(sig, prior)


def jjx_cli() -> int:
    """Run the `jjx` CLI and return an exit code.

    The `jjx` CLI can be run when the `jjx` package is installed in the charm's venv,
    or as a tool outside the charm's venv.
    """
    # Split jjx's own flags from extra pytest args at --.
    jjx_args, cli_pytest_args = _split_cli_args(sys.argv[1:])

    # Handle explicit down command.
    if jjx_args and jjx_args[0] == "down":
        teardown_all_models()
        return 0

    # Preflight: clean up any stale state from a previous run.
    if (_engine._project_root() / _engine.STATE_DIR_NAME).exists():
        running = _engine._running_workload_container()
        if running is not None:
            sys.stderr.write(f"Container {running.name} is up\nRun 'jjx down' to tear down\n")
            return 1
    teardown_all_models()

    # Preflight: fail fast if Docker is not available, rather than letting pytest
    # surface a confusing 'failed to connect to the docker API' error later.
    try:
        _engine._check_docker_available()
    except _engine.CliError as exc:
        if exc.message:
            sys.stderr.write(exc.message + "\n")
        return exc.exit_code

    charm_root = _engine._project_root()
    # Preflight: warn about an unset charm-user here, where the user sees it.
    # Deploy checks charm-user when the test harness invokes the juju shim,
    # but its stderr is captured by pytest, so only the CLI's preflight
    # warning reaches the terminal.
    if (charm_root / "charmcraft.yaml").exists() and _cmd_deploy.charm_user_unset(charm_root):
        sys.stderr.write(
            "warning: charm-user is not set in charmcraft.yaml; jjx runs charm code as "
            "non-root (user 170). Set `charm-user: non-root` in charmcraft.yaml to "
            "match jjx's behavior.\n"
        )
        sys.stderr.flush()

    detach = "-d" in jjx_args

    # Extract -p flag for port publishing.
    publish = None
    if "-p" in jjx_args:
        idx = jjx_args.index("-p")
        if idx + 1 < len(jjx_args):
            publish = jjx_args[idx + 1]
            if not re.match(r"^\d+:\d+$", publish):
                sys.stderr.write(
                    f"ERROR: Invalid port format '{publish}': expected <number>:<number>\n"
                )
                return 2

    try:
        pytest_args = jjx_pytest_args(charm_root, cli_pytest_args)
        if "--no-juju-teardown" in pytest_args and not _has_pytest_jubilant(charm_root):
            raise _engine.CliError(
                "ERROR: pytest-jubilant is not in the 'integration' dependency group."
            )
    except _engine.CliError as exc:
        if exc.message:
            sys.stderr.write(exc.message + "\n")
        return exc.exit_code
    except KeyboardInterrupt:
        # Ctrl-C during preflight: nothing has been created yet.
        return 130  # Standard exit code for SIGINT

    placeholder_charm = charm_root / "placeholder.charm"
    placeholder_charm.touch()

    env = os.environ.copy()
    env["CHARM_PATH"] = str(placeholder_charm)
    # The inner `uv run` must resolve the charm's project, not whatever venv
    # launched us. When jjx is run via uvx (or `uv run`), VIRTUAL_ENV points
    # at the launcher's venv; if inherited, uv may reuse it instead of creating
    # one in the charm dir, causing pytest to run from the wrong project root.
    env.pop("VIRTUAL_ENV", None)
    if publish:
        env["JJX_PUBLISH"] = publish
        external_port, _ = publish.split(":", 1)
    cmd = [
        "uv",
        "run",
        *jjx_pytest_env_args(charm_root),
        "--group",
        "integration",
        "pytest",
        *pytest_args,
    ]

    try:
        returncode, interrupted = _run_pytest_session(cmd, env)
        # The placeholder charm has done its job as soon as the run is over.
        placeholder_charm.unlink()
        if interrupted:
            # The test tree was interrupted: tear models down before jjx exits.
            print()  # separate the teardown output from the interrupt report
            teardown_all_models()
            return 130  # Standard exit code for SIGINT
        container = _engine._running_workload_container()
        if container is None:
            print()  # separate the teardown output from the test report
            teardown_all_models()
            return returncode
        if publish:
            workload_line = f"Workload running at 127.0.0.1:{external_port}"
        else:
            workload_line = f"Workload running at {container.ip_address}"
        print(f"\n{workload_line}", flush=True)
        # List user-facing endpoints for virtual COS charms (loki, etc.)
        # so the user can interact with them directly.
        cos_lines = _cos_endpoint_lines()
        if cos_lines:
            print(flush=True)
            for line in cos_lines:
                print(line, flush=True)
        if detach:
            print("\nRun 'jjx down' to tear down")
            return returncode
        print("\nPress Ctrl-C to tear down", flush=True)
        _pause_until_signalled()
        print()
        teardown_all_models()
        return 130  # Standard exit code for SIGINT
    except KeyboardInterrupt:
        # Destroy all models on Ctrl+C racing the pytest tree's return: a ^C
        # can arrive while the final prints are going out, before the pause.
        # The pytest and pause phases use non-raising handlers, so a press
        # landing during either is handled by that phase's own choreography.
        teardown_all_models()
        return 130  # Standard exit code for SIGINT
    finally:
        # Safety net: remove the placeholder even when the run ends by
        # exception, so no stray .charm file is ever left behind.
        placeholder_charm.unlink(missing_ok=True)
