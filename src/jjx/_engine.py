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
SOCKET_FILE_NAME = "socket"
GITIGNORE_FILE_NAME = ".gitignore"
SITECUSTOMIZE_FILE_NAME = "sitecustomize.py"

PEBBLE_RELEASES_API = "https://api.github.com/repos/canonical/pebble/releases/latest"
PEBBLE_RELEASES_DOWNLOAD = "https://github.com/canonical/pebble/releases/download/{tag}/{asset}"

# Injected into the charm's Python environment to rewrite connect(0.0.0.0, port)
# to connect(container_ip, port), mirroring the K8s pod shared-network-namespace model.
_SITECUSTOMIZE_PY = """\
import os as _os
import socket as _socket

_container_ip = _os.environ.get("JJX_CONTAINER_IP", "")
if _container_ip:
    _orig_connect = _socket.socket.connect

    def _patched_connect(self, address):
        if isinstance(address, tuple) and address[0] in ("0.0.0.0", "::"):
            address = (_container_ip, *address[1:])
        return _orig_connect(self, address)

    _socket.socket.connect = _patched_connect
"""


@dataclass
class CliError(Exception):
    """Error type with exit code for CLI usage and runtime failures."""

    message: str
    exit_code: int = 1


@dataclass(frozen=True)
class ContainerDetails:
    """Resolved Docker metadata for one workload container."""

    name: str
    ip_address: str
    running: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _project_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / STATE_DIR_NAME / STATE_FILE_NAME).exists():
            return candidate
    return cwd


def _jjx_dir() -> Path:
    env_dir = os.environ.get("JJX_STATE_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return _project_root() / STATE_DIR_NAME


def _state_path() -> Path:
    return _jjx_dir() / STATE_FILE_NAME


def _hook_tools_dir() -> Path:
    return _jjx_dir() / HOOK_TOOLS_DIR_NAME


def _socket_path() -> Path:
    return _jjx_dir() / SOCKET_FILE_NAME


def _sitecustomize_path() -> Path:
    return _jjx_dir() / SITECUSTOMIZE_FILE_NAME


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


def _cleanup_model_artifacts() -> None:
    """Remove all project-local runtime state from .jjx/.

    The pebble cache at ~/.cache/jjx/pebble-bin is preserved for reuse.
    """
    shutil.rmtree(_jjx_dir(), ignore_errors=True)


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
    return safe[:128]


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
    tmpfs_mounts: list[str] | None = None,
    publish: str | None = None,
    env: dict[str, str] | None = None,
    user: str | None = None,
    workdir: str | None = None,
    entrypoint: str | None = None,
    command: list[str] | None = None,
    network: str | None = None,
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

    if network:
        cmd.extend(["--network", network])

    if publish:
        cmd.extend(["--publish", publish])

    for src, dst, read_only in mounts or []:
        mode = "ro" if read_only else "rw"
        cmd.extend(["--volume", f"{src}:{dst}:{mode}"])

    for tmpfs in tmpfs_mounts or []:
        cmd.extend(["--tmpfs", tmpfs])

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
    print(f"Removed container {container_name}")


def _docker_container_details(container_name: str) -> ContainerDetails:
    try:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Name}}|{{.State.Running}}|{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container_name,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CliError(
            f"failed to inspect container {container_name}: {exc.stderr.strip()}"
        ) from None

    name, separator, remainder = proc.stdout.rstrip("\n").partition("|")
    running_str, separator2, ip_address = remainder.partition("|")
    if not separator or not separator2:
        raise CliError(f"failed to inspect container {container_name}: unexpected docker output")

    running_value = running_str.strip().lower()
    if running_value not in {"true", "false"}:
        raise CliError(f"failed to inspect container {container_name}: unexpected running state")

    running = running_value == "true"
    normalized_name = name.strip().lstrip("/") or container_name
    ip_address = ip_address.strip()
    if running and not ip_address:
        raise CliError(f"container {normalized_name} has no IP address (is it running?)")

    return ContainerDetails(
        name=normalized_name,
        ip_address=ip_address,
        running=running,
    )


def _docker_container_ip(container_name: str) -> str:
    return _docker_container_details(container_name).ip_address


def _running_workload_container() -> ContainerDetails | None:
    state = _load_state()
    for model_state in state.get("models", {}).values():
        apps = model_state.get("apps", {})
        if not isinstance(apps, dict):
            continue
        for app_state in apps.values():
            if not isinstance(app_state, dict):
                continue
            container_name = app_state.get("container_name")
            if not isinstance(container_name, str) or not container_name:
                continue
            try:
                container = _docker_container_details(container_name)
            except CliError:
                continue
            if container.running:
                return container
    return None


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
    cache_path = Path.home() / ".cache" / "jjx" / "pebble-bin"
    if cache_path.exists():
        return cache_path

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

    cache_path.parent.mkdir(parents=True, exist_ok=True)

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
                cache_path.write_bytes(extracted.read())
    except (HTTPError, URLError, TimeoutError, tarfile.TarError, OSError) as exc:
        raise CliError(f"failed to download pebble: {exc}") from None

    cache_path.chmod(0o755)
    return cache_path


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


def _project_venv_python() -> Path | None:
    venv_bin = _project_root() / ".venv" / "bin"
    for name in ("python3", "python"):
        candidate = venv_bin / name
        if candidate.exists():
            return candidate
    return None


def _project_venv_bin() -> Path | None:
    venv_bin = _project_root() / ".venv" / "bin"
    if venv_bin.is_dir():
        return venv_bin
    return None


def _python_can_import_jjx(python_exe: Path) -> bool:
    result = subprocess.run(
        [str(python_exe), "-c", "import jjx"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _python_executable() -> str:
    project_python = _project_venv_python()
    if project_python and _python_can_import_jjx(project_python):
        return str(project_python)

    python_exe = Path(sys.executable)
    if python_exe.exists() and _python_can_import_jjx(python_exe):
        return str(python_exe)
    raise CliError(
        f"no usable Python runtime for jjx hook tools (sys.executable={sys.executable!r})"
    )


def _charm_python_executable() -> str:
    project_python = _project_venv_python()
    if project_python is not None:
        return str(project_python)

    python_exe = Path(sys.executable)
    if python_exe.exists():
        return str(python_exe)
    raise CliError(f"sys.executable is not usable: {sys.executable!r}")


def _ensure_hook_tools(python_exe: str) -> None:
    tools = [
        "application-version-set",
        "config-get",
        "status-get",
        "status-set",
        "is-leader",
        "juju-log",
    ]
    root = _hook_tools_dir()
    root.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        path = root / tool
        path.write_text(
            (
                "#!/bin/sh\n"
                f'exec "{python_exe}" -c \'import sys; '
                "import jjx; "
                f'raise SystemExit(jjx.run_hook_tool("{tool}", sys.argv[1:]))\' "$@"\n'
            ),
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

    env = os.environ.copy()
    env.update(
        {
            "JUJU_DISPATCH_PATH": dispatch_path,
            "JUJU_HOOK_NAME": hook_name,
            "JUJU_MODEL_NAME": model_name,
            "JUJU_MODEL_UUID": model_state["uuid"],
            "JUJU_UNIT_NAME": unit_name,
            "JUJU_VERSION": "3.6.0",
            # Inside bubblewrap, the charm directory is bind-mounted at /charm,
            # matching the path real Juju uses for JUJU_CHARM_DIR.
            "JUJU_CHARM_DIR": "/charm",
            # Hook tools run inside bubblewrap where cwd is /charm; they need
            # to find .jjx/state.json, so pass the state directory explicitly.
            "JJX_STATE_DIR": str(_jjx_dir()),
        }
    )

    if workload_name:
        env["JUJU_WORKLOAD_NAME"] = workload_name
    else:
        env.pop("JUJU_WORKLOAD_NAME", None)

    path_entries = [str(_hook_tools_dir())]
    project_venv_bin = _project_venv_bin()
    if project_venv_bin is not None:
        path_entries.append(str(project_venv_bin))
    path_entries.append(env.get("PATH", ""))
    env["PATH"] = ":".join(entry for entry in path_entries if entry)
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
        # Bind the staged charm directory to /charm so that JUJU_CHARM_DIR=/charm
        # matches real Juju, and charms that hardcode /charm paths work correctly.
        "--bind",
        str(charm_root),
        "/charm",
        "--dir",
        "/charm/containers",
        "--dir",
        f"/charm/containers/{workload_name}",
        "--bind",
        str(_socket_path()),
        pebble_socket,
        "--chdir",
        "/charm",
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

    hook_tool_python = _python_executable()
    _ensure_hook_tools(hook_tool_python)
    charm_python = _charm_python_executable()

    runtime = app_state.get("runtime") or {}
    charm_root = Path(runtime.get("charm_dir", app_state["charm_source"])).resolve()
    charm_entrypoint = charm_root / "src" / "charm.py"
    if not charm_entrypoint.exists():
        raise CliError(f"charm entrypoint not found: {charm_entrypoint}")
    # Inside bubblewrap, charm_root is bind-mounted at /charm, so the
    # entrypoint path the charm Python receives must use the sandbox path.
    charm_entry = "/charm/src/charm.py"

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

    container_name = app_state.get("container_name", "")
    if not container_name:
        raise CliError(f"application {app_name} has no container name in state")
    container = _docker_container_details(container_name)
    if not container.running:
        raise CliError(f"container {container.name} is not running")
    env["JJX_CONTAINER_IP"] = container.ip_address

    sitecustomize_path = _sitecustomize_path()
    sitecustomize_path.parent.mkdir(parents=True, exist_ok=True)
    sitecustomize_path.write_text(
        _SITECUSTOMIZE_PY,
        encoding="utf-8",
    )
    sitecustomize_parent = sitecustomize_path.parent
    env["PYTHONPATH"] = (
        f"{sitecustomize_parent}:{env.get('PYTHONPATH', '')}"
        if env.get("PYTHONPATH")
        else str(sitecustomize_parent)
    )

    cmd = _bwrap_cmd(charm_root, workload) + [charm_python, charm_entry]
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
        "version": app_state.get("workload_version", ""),
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
