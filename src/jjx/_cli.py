"""Entrypoints for the `jjx` CLI and the `juju` compatibility CLI."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # ty: ignore[unresolved-import]

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
    if command == "version":
        return _cmd_misc.version(rest)
    if command == "show-model":
        return _cmd_misc.show_model(rest, model)
    if command == "models":
        return _cmd_misc.models(rest)

    raise _engine.CliError(f"unknown command: {command}")


def juju_dispatch(argv: list[str]) -> int:
    """Run a Juju-compatible argv vector and return an exit code."""
    try:
        return run_juju_command(argv)
    except _engine.CliError as exc:
        if exc.message:
            sys.stderr.write(exc.message + "\n")
        return exc.exit_code


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
    default_args = ["tests/integration", "--no-juju-teardown"]
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

    charm_root = _engine._project_root()
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
        proc = subprocess.run(cmd, env=env, check=False)
        placeholder_charm.unlink()
        container = _engine._running_workload_container()
        if container is None:
            teardown_all_models()
            return proc.returncode
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
            return proc.returncode
        print("\nPress Ctrl-C to tear down", flush=True)
        signal.pause()
        return proc.returncode
    except KeyboardInterrupt:
        # Destroy all models on Ctrl+C
        print()
        teardown_all_models()
        return 130  # Standard exit code for SIGINT
