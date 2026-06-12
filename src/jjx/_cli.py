"""Entrypoints for the `jjx` CLI and the `juju` compatibility CLI."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

from . import (
    _cmd_add_model,
    _cmd_config,
    _cmd_debug_log,
    _cmd_deploy,
    _cmd_destroy_model,
    _cmd_remove_application,
    _cmd_hook_tool,
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
    for model_name in state.get("models", {}):
        _cmd_destroy_model.destroy_model([model_name])


def _cleanup_placeholder_charm(path: Path) -> None:
    if path.exists():
        path.unlink()


def jjx_pytest_env_args(charm_root: Path) -> list[str]:
    """Return uv-run args that keep jjx resolution consistent with launch mode."""
    charm_venv_dir = (charm_root / ".venv").absolute()
    current_python = Path(sys.executable).absolute()

    # Case 1: running from the charm venv; pin uv to the current interpreter.
    if charm_venv_dir in current_python.parents:
        return ["--python", sys.executable]

    package_root = Path(__file__).resolve().parents[2]

    # Case 2: running from a local checkout/tool workflow; use editable source.
    if (package_root / "pyproject.toml").exists():
        return ["--with-editable", str(package_root)]

    # Case 3: fallback when no local checkout is available.
    return ["--with", "jjx"]


def jjx_cli() -> int:
    """Run the `jjx` CLI and return an exit code.

    The `jjx` CLI can be run when the `jjx` package is installed in the charm's venv,
    or as a tool outside the charm's venv.
    """
    # Handle explicit down command.
    if len(sys.argv) > 1 and sys.argv[1] == "down":
        teardown_all_models()
        return 0

    charm_root = _engine._project_root()
    placeholder_charm = charm_root / "placeholder.charm"
    if not placeholder_charm.exists():
        placeholder_charm.touch()

    env = os.environ.copy()
    env["CHARM_PATH"] = str(placeholder_charm)

    cmd = [
        "uv",
        "run",
        *jjx_pytest_env_args(charm_root),
        "--group",
        "integration",
        "pytest",
        "tests/integration",
        "--no-juju-teardown",
    ]

    try:
        proc = subprocess.run(cmd, env=env)
        container = _engine._running_workload_container()
        if container is None:
            teardown_all_models()
            _cleanup_placeholder_charm(placeholder_charm)
            return proc.returncode
        print(
            f"\nStarted workload container {container.name} with IP {container.ip_address}"
            "\n\nPress Ctrl-C to tear down",
            flush=True,
        )
        signal.pause()
        return proc.returncode
    except KeyboardInterrupt:
        # Destroy all models on Ctrl+C
        print()
        teardown_all_models()
        _cleanup_placeholder_charm(placeholder_charm)
        return 130  # Standard exit code for SIGINT
