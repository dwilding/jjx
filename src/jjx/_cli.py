"""Entrypoints for the `jjx` CLI and the `juju` compatibility CLI."""

from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import subprocess
import sys

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


def jjx_pytest_args(charm_root: Path) -> list[str]:
    """Return pytest args from [tool.jjx].pytest-args, or defaults if unset."""
    default_args = ["tests/integration", "--no-juju-teardown"]
    pyproject = charm_root / "pyproject.toml"
    if not pyproject.exists():
        return default_args

    try:
        with pyproject.open("rb") as fp:
            config = tomllib.load(fp)
    except tomllib.TOMLDecodeError as exc:
        raise _engine.CliError(f"ERROR: Invalid pyproject.toml: {exc}") from exc

    pytest_args = config.get("tool", {}).get("jjx", {}).get("pytest-args")
    if pytest_args is None:
        return default_args

    if not isinstance(pytest_args, list) or not all(isinstance(arg, str) for arg in pytest_args):
        raise _engine.CliError("ERROR: [tool.jjx].pytest-args must be an array of strings")

    return pytest_args


def jjx_cli() -> int:
    """Run the `jjx` CLI and return an exit code.

    The `jjx` CLI can be run when the `jjx` package is installed in the charm's venv,
    or as a tool outside the charm's venv.
    """
    # Handle explicit down command.
    if len(sys.argv) > 1 and sys.argv[1] == "down":
        teardown_all_models()
        return 0

    # Preflight: clean up any stale state from a previous run.
    if (_engine._project_root() / _engine.STATE_DIR_NAME).exists():
        running = _engine._running_workload_container()
        if running is not None:
            sys.stderr.write(f"Container {running.name} is up\nRun 'jjx down' to tear down\n")
            return 1
        teardown_all_models()

    detach = "-d" in sys.argv

    # Extract -p flag for Docker port publishing.
    docker_publish = None
    publish_output = ""
    if "-p" in sys.argv:
        idx = sys.argv.index("-p")
        if idx + 1 < len(sys.argv):
            docker_publish = sys.argv[idx + 1]
            if not re.match(r"^\d+:\d+$", docker_publish):
                sys.stderr.write(
                    f"ERROR: Invalid port format '{docker_publish}': expected <number>:<number>\n"
                )
                return 2

    charm_root = _engine._project_root()
    try:
        pytest_args = jjx_pytest_args(charm_root)
    except _engine.CliError as exc:
        if exc.message:
            sys.stderr.write(exc.message + "\n")
        return exc.exit_code

    placeholder_charm = charm_root / "placeholder.charm"
    placeholder_charm.touch()

    env = os.environ.copy()
    env["CHARM_PATH"] = str(placeholder_charm)
    if docker_publish:
        env["JJX_DOCKER_PUBLISH"] = docker_publish
        external_port, internal_port = docker_publish.split(":", 1)
        publish_output = (
            f"\n\nPublished container port {internal_port} to 127.0.0.1:{external_port}"
        )
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
        proc = subprocess.run(cmd, env=env)
        placeholder_charm.unlink()
        container = _engine._running_workload_container()
        if container is None:
            teardown_all_models()
            return proc.returncode
        print(
            f"\nStarted workload container {container.name} with IP {container.ip_address}{publish_output}",
            flush=True,
        )
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
