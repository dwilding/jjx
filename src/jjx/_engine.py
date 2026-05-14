"""Runtime engine for the ``juju`` compatibility CLI.

This module implements the minimal Juju-like behavior described in DESIGN.md.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


STATE_DIR_NAME = ".jjx"
STATE_FILE_NAME = "state.json"
HOOK_TOOLS_DIR_NAME = "hook-tools"
PEBBLE_DIR_NAME = "pebble"
SOCKET_FILE_NAME = "socket"
GITIGNORE_FILE_NAME = ".gitignore"

PEBBLE_RELEASES_API = "https://api.github.com/repos/canonical/pebble/releases/latest"
PEBBLE_RELEASES_DOWNLOAD = "https://github.com/canonical/pebble/releases/download/{tag}/{asset}"

JJX_CACHED_PEBBLE_BIN_ENV = "JJX_CACHED_PEBBLE_BIN"


@dataclass
class CliError(Exception):
    """Error type with exit code for CLI usage and runtime failures."""

    message: str
    exit_code: int = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _project_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / STATE_DIR_NAME / STATE_FILE_NAME).exists():
            return candidate
    return cwd


def _jjx_dir() -> Path:
    return _project_root() / STATE_DIR_NAME


def _state_path() -> Path:
    return _jjx_dir() / STATE_FILE_NAME


def _hook_tools_dir() -> Path:
    return _jjx_dir() / HOOK_TOOLS_DIR_NAME


def _pebble_dir() -> Path:
    return _jjx_dir() / PEBBLE_DIR_NAME


def _socket_path() -> Path:
    return _jjx_dir() / SOCKET_FILE_NAME


def _gitignore_path() -> Path:
    return _jjx_dir() / GITIGNORE_FILE_NAME


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    return {}


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"models": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"invalid state file: {path}: {exc}") from None


def _save_state(state: dict[str, Any]) -> None:
    jjx = _jjx_dir()
    jjx.mkdir(parents=True, exist_ok=True)
    _gitignore_path().write_text("*\n", encoding="utf-8")
    _hook_tools_dir().mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


def _cleanup_model_artifacts() -> None:
    for path in (
        _state_path(),
        _socket_path(),
        _pebble_dir(),
        _hook_tools_dir(),
        _jjx_dir() / "charm",
    ):
        _remove_path(path)

    jjx_dir = _jjx_dir()
    if not jjx_dir.exists():
        return
    try:
        next(jjx_dir.iterdir())
    except StopIteration:
        jjx_dir.rmdir()


def _append_log(model_state: dict[str, Any], message: str) -> None:
    logs = model_state.setdefault("logs", [])
    logs.append(f"{_now_iso()} {message}")


def _require_model_name(state: dict[str, Any], model: str | None) -> str:
    if model:
        if model not in state.get("models", {}):
            raise CliError(f"ERROR model {model} does not exist")
        return model

    models = state.get("models", {})
    if len(models) == 1:
        return next(iter(models))

    raise CliError("ERROR no model selected; use --model")


def _sanitize_container_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", name)
    return f"jjx-{safe}"[:128]


def _split_resource(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise CliError(f"invalid --resource value: {raw}")
    key, value = raw.split("=", 1)
    return key, value


def _default_config(charm_root: Path) -> dict[str, Any]:
    data = _read_yaml(charm_root / "charmcraft.yaml")
    config = data.get("config", {}).get("options", {})
    defaults: dict[str, Any] = {}
    if not isinstance(config, dict):
        return defaults
    for key, spec in config.items():
        if isinstance(spec, dict) and "default" in spec:
            defaults[key] = spec["default"]
    return defaults


def _discover_charm_source(charm_path: str, app_name: str) -> Path:
    charm_arg = Path(charm_path)
    cwd = _project_root()

    if charm_arg.suffix == ".charm":
        candidate = charm_arg if charm_arg.is_absolute() else cwd / charm_arg
        if candidate.parent == cwd and (cwd / "src" / "charm.py").exists():
            return cwd

    if (cwd / "src" / "charm.py").exists() and (cwd / "charmcraft.yaml").exists():
        return cwd

    for child in cwd.iterdir() if cwd.exists() else []:
        if not child.is_dir():
            continue
        if not (child / "src" / "charm.py").exists():
            continue
        text = (child / "charmcraft.yaml").read_text(encoding="utf-8")
        if f"name: {app_name}" in text:
            return child

    raise CliError("unable to discover charm source for deployment")


def _status_dict(
    status: str = "maintenance",
    message: str = "initializing",
) -> dict[str, Any]:
    return {"status": status, "message": message, "status-data": {}}


def _docker_run(
    image: str,
    container_name: str,
    mounts: list[tuple[str, str, bool]] | None = None,
    env: dict[str, str] | None = None,
    user: str | None = None,
    workdir: str | None = None,
    entrypoint: str | None = None,
    command: list[str] | None = None,
) -> str:
    cmd = [
        "docker",
        "run",
        "--detach",
        "--restart",
        "unless-stopped",
        "--name",
        container_name,
    ]

    for src, dst, read_only in mounts or []:
        mode = "ro" if read_only else "rw"
        cmd.extend(["--volume", f"{src}:{dst}:{mode}"])

    for key, value in (env or {}).items():
        cmd.extend(["--env", f"{key}={value}"])

    if user:
        cmd.extend(["--user", user])

    if workdir:
        cmd.extend(["--workdir", workdir])

    if entrypoint:
        cmd.extend(["--entrypoint", entrypoint])

    cmd.append(image)
    if command:
        cmd.extend(command)

    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise CliError(exc.stderr.strip() or exc.stdout.strip() or "docker run failed") from None
    return proc.stdout.strip()


def _docker_rm(container_name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", container_name],
        capture_output=True,
        text=True,
    )


def _docker_list_model_containers(model_name: str) -> list[str]:
    model_prefix = _sanitize_container_name(f"{model_name}-")
    try:
        proc = subprocess.run(
            ["docker", "ps", "--all", "--format", "{{.Names}}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []

    names = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return [name for name in names if name.startswith(model_prefix)]


def _resolve_pebble_binary() -> Path:
    local_cache_path = _jjx_dir() / "bin" / "pebble"
    if local_cache_path.exists():
        return local_cache_path

    # Check for external cache directory via environment variable
    external_cache_dir = os.environ.get(JJX_CACHED_PEBBLE_BIN_ENV)
    external_cache_path = None
    if external_cache_dir:
        external_cache_path = Path(external_cache_dir)
        if external_cache_path.exists():
            # Copy from external cache to local cache
            local_cache_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(external_cache_path, local_cache_path)
            local_cache_path.chmod(0o755)
            return local_cache_path

    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    arch = arch_map.get(os.uname().machine)
    if not arch:
        raise CliError(f"unsupported architecture for pebble download: {os.uname().machine}")

    try:
        request = Request(PEBBLE_RELEASES_API, headers={"User-Agent": "jjx"})
        with urlopen(request, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise CliError(f"failed to query pebble release metadata: {exc}") from None

    tag = release.get("tag_name")
    if not tag:
        raise CliError("pebble release metadata did not include a tag name")

    asset_name = f"pebble_{tag}_linux_{arch}.tar.gz"
    assets = release.get("assets", [])
    asset = next(
        (item for item in assets if isinstance(item, dict) and item.get("name") == asset_name),
        None,
    )
    if asset is None:
        raise CliError(f"pebble release asset not found: {asset_name}")

    download_url = asset.get("browser_download_url") or PEBBLE_RELEASES_DOWNLOAD.format(
        tag=tag,
        asset=asset_name,
    )
    if not download_url:
        raise CliError(f"pebble asset has no download URL: {asset_name}")

    # Determine where to download to: external cache if configured, otherwise local
    download_path = external_cache_path if external_cache_path else local_cache_path
    download_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        request = Request(download_url, headers={"User-Agent": "jjx"})
        with urlopen(request, timeout=60) as response:
            archive_bytes = io.BytesIO(response.read())
            with tarfile.open(fileobj=archive_bytes, mode="r:gz") as archive:
                member = next(
                    (
                        item
                        for item in archive.getmembers()
                        if item.name.endswith("/pebble") or item.name == "pebble"
                    ),
                    None,
                )
                if member is None:
                    raise CliError(f"pebble archive did not contain a pebble binary: {asset_name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise CliError(f"failed to extract pebble binary from {asset_name}")
                download_path.write_bytes(extracted.read())
    except (HTTPError, URLError, TimeoutError, tarfile.TarError, OSError) as exc:
        raise CliError(f"failed to download pebble: {exc}") from None

    download_path.chmod(0o755)

    # If we downloaded to external cache, copy to local cache
    if external_cache_path and external_cache_path != local_cache_path:
        local_cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(download_path, local_cache_path)
        local_cache_path.chmod(0o755)

    return local_cache_path


def _staged_pebble_binary(pebble_binary: Path) -> Path:
    """Return a daemon-visible Pebble binary path for Docker bind mounts.

    Some Docker setups cannot bind deep or unusual project paths reliably.
    Staging in ``/tmp`` keeps the mount path simple and stable.
    """

    cache_root = Path.home() / ".cache"
    staged = cache_root / "jjx" / f"pebble-{os.getuid()}"
    staged.parent.mkdir(parents=True, exist_ok=True)
    if staged.exists() and staged.is_file():
        src_stat = pebble_binary.stat()
        dst_stat = staged.stat()
        if dst_stat.st_size == src_stat.st_size and dst_stat.st_mtime >= src_stat.st_mtime:
            return staged

    shutil.copy2(pebble_binary, staged)
    staged.chmod(0o755)
    return staged


def _wait_for_socket(socket_path: Path, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    connect_paths = [str(socket_path)]
    try:
        rel = socket_path.relative_to(_project_root())
        rel_path = str(rel)
        if rel_path and rel_path not in connect_paths:
            # Prefer shorter relative paths to avoid AF_UNIX path-length limits.
            connect_paths.insert(0, rel_path)
    except ValueError:
        pass

    while time.monotonic() < deadline:
        if socket_path.exists():
            for connect_path in connect_paths:
                try:
                    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    probe.settimeout(0.2)
                    probe.connect(connect_path)
                    probe.close()
                    return
                except OSError:
                    continue
        time.sleep(0.1)
    raise CliError(f"timed out waiting for pebble socket: {socket_path}")


def _python_executable() -> str:
    python_exe = sys.executable
    if python_exe and Path(python_exe).exists():
        return python_exe
    raise CliError(f"sys.executable is not usable: {python_exe!r}")


def _ensure_hook_tools(python_exe: str) -> None:
    tools = ["config-get", "status-get", "status-set", "is-leader", "juju-log"]
    root = _hook_tools_dir()
    root.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        path = root / tool
        path.write_text(
            (f'#!/bin/sh\nexec {python_exe} -m jjx._cli _hook-tool {tool} "$@"\n'),
            encoding="utf-8",
        )
        path.chmod(0o755)


def _stage_runtime_charm(charm_source: Path, runtime_charm_dir: Path) -> None:
    runtime_charm_dir.mkdir(parents=True, exist_ok=True)

    src_link = runtime_charm_dir / "src"
    if src_link.exists() or src_link.is_symlink():
        if src_link.is_dir() and not src_link.is_symlink():
            shutil.rmtree(src_link)
        else:
            src_link.unlink()
    src_link.symlink_to(charm_source / "src", target_is_directory=True)

    data = _read_yaml(charm_source / "charmcraft.yaml")

    metadata: dict[str, Any] = {}
    for key in (
        "name",
        "summary",
        "description",
        "containers",
        "resources",
        "requires",
        "provides",
        "peers",
        "storage",
    ):
        if key in data:
            metadata[key] = data[key]

    (runtime_charm_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False),
        encoding="utf-8",
    )
    (runtime_charm_dir / "config.yaml").write_text(
        yaml.safe_dump(data.get("config", {}), sort_keys=False),
        encoding="utf-8",
    )


def _ensure_runtime_layout(app_state: dict[str, Any]) -> None:
    charm_source = Path(app_state["charm_source"]).resolve()
    runtime_charm_dir = _jjx_dir() / "charm"
    _stage_runtime_charm(charm_source, runtime_charm_dir)
    app_state["runtime"] = {"charm_dir": str(runtime_charm_dir)}


def _build_charm_env(
    model_name: str,
    model_state: dict[str, Any],
    app_name: str,
    app_state: dict[str, Any],
    hook_name: str,
    dispatch_path: str,
    workload_name: str | None = None,
) -> dict[str, str]:
    unit_name = app_state.get("unit", f"{app_name}/0")
    runtime = app_state.get("runtime") or {}
    charm_root = runtime.get("charm_dir", app_state["charm_source"])

    env = os.environ.copy()
    env.update(
        {
            "JUJU_DISPATCH_PATH": dispatch_path,
            "JUJU_HOOK_NAME": hook_name,
            "JUJU_MODEL_NAME": model_name,
            "JUJU_MODEL_UUID": model_state["uuid"],
            "JUJU_UNIT_NAME": unit_name,
            "JUJU_VERSION": "3.6.0",
            "JUJU_CHARM_DIR": charm_root,
        }
    )

    if workload_name:
        env["JUJU_WORKLOAD_NAME"] = workload_name
    else:
        env.pop("JUJU_WORKLOAD_NAME", None)

    env["PATH"] = f"{_hook_tools_dir()}:{env.get('PATH', '')}"
    return env


def _bwrap_cmd(charm_root: Path, workload_name: str) -> list[str]:
    project_root = _project_root()
    pebble_socket = f"/charm/containers/{workload_name}/pebble.socket"
    return [
        "bwrap",
        "--tmpfs",
        "/",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/etc",
        "/etc",
        "--ro-bind",
        "/home",
        "/home",
        "--bind",
        str(project_root),
        str(project_root),
        "--bind",
        "/tmp",
        "/tmp",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--dir",
        "/charm",
        "--dir",
        "/charm/containers",
        "--dir",
        f"/charm/containers/{workload_name}",
        "--bind",
        str(charm_root),
        str(charm_root),
        "--bind",
        str(_socket_path()),
        pebble_socket,
        "--chdir",
        str(charm_root),
        "--",
    ]


def _run_charm_event(
    model_name: str,
    app_name: str,
    hook_name: str,
    dispatch_path: str,
    workload_name: str | None = None,
) -> None:
    state = _load_state()
    model_state = state["models"][model_name]
    app_state = model_state["apps"][app_name]

    _ensure_runtime_layout(app_state)
    _save_state(state)

    python_exe = _python_executable()
    _ensure_hook_tools(python_exe)

    runtime = app_state.get("runtime") or {}
    charm_root = Path(runtime.get("charm_dir", app_state["charm_source"])).resolve()
    charm_entrypoint = charm_root / "src" / "charm.py"
    if not charm_entrypoint.exists():
        raise CliError(f"charm entrypoint not found: {charm_entrypoint}")

    _wait_for_socket(_socket_path())

    workload = workload_name or app_state.get("workload")
    if not isinstance(workload, str) or not workload:
        raise CliError(f"application {app_name} has no workload container configured")
    env = _build_charm_env(
        model_name=model_name,
        model_state=model_state,
        app_name=app_name,
        app_state=app_state,
        hook_name=hook_name,
        dispatch_path=dispatch_path,
        workload_name=workload,
    )

    site_paths = [p for p in sys.path if "site-packages" in p and Path(p).exists()]
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        site_paths.append(existing_pythonpath)
    if site_paths:
        env["PYTHONPATH"] = ":".join(site_paths)

    cmd = _bwrap_cmd(charm_root, workload) + [python_exe, str(charm_entrypoint)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)

    state = _load_state()
    model_state = state["models"][model_name]
    _append_log(model_state, f"event {hook_name} exit={proc.returncode}")
    if proc.stdout.strip():
        _append_log(model_state, f"event {hook_name} stdout:\n{proc.stdout.strip()}")
    if proc.stderr.strip():
        _append_log(model_state, f"event {hook_name} stderr:\n{proc.stderr.strip()}")
    _save_state(state)

    if proc.returncode != 0:
        raise CliError(
            proc.stderr.strip() or f"hook {hook_name} failed with exit code {proc.returncode}"
        )


def _run_deploy_event_flow(model_name: str, app_name: str, workload_name: str) -> None:
    _run_charm_event(model_name, app_name, "config-changed", "hooks/config-changed")
    _run_charm_event(
        model_name,
        app_name,
        f"{workload_name}-pebble-ready",
        f"hooks/{workload_name}-pebble-ready",
        workload_name=workload_name,
    )


def _run_config_event_flow(model_name: str, app_name: str) -> None:
    _run_charm_event(model_name, app_name, "config-changed", "hooks/config-changed")


def _status_for_app(app_name: str, app_state: dict[str, Any]) -> dict[str, Any]:
    app_stat = app_state.get("app_status") or _status_dict("maintenance", "unknown")
    unit_stat = app_state.get("unit_status") or _status_dict("maintenance", "unknown")
    unit_name = app_state.get("unit", f"{app_name}/0")

    current = app_stat.get("status", "unknown")
    message = app_stat.get("message", "")

    return {
        "charm": app_state.get("charm", "local:jjx"),
        "charm-origin": "local",
        "charm-name": app_name,
        "charm-rev": 0,
        "exposed": False,
        "application-status": {
            "current": current,
            "message": message,
        },
        "units": {
            unit_name: {
                "workload-status": {
                    "current": unit_stat.get("status", current),
                    "message": unit_stat.get("message", message),
                },
                "juju-status": {
                    "current": "idle",
                    "message": "ready",
                },
                "leader": True,
            }
        },
    }
