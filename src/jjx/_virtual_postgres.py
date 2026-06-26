"""Virtual postgresql-k8s provider.

This module implements a minimal "virtual charm" for postgresql-k8s that runs
a real PostgreSQL instance in a Docker container and writes the relation data
that a ``DatabaseProvides`` charm would write, so that charms using
``DatabaseRequires`` from the data_interfaces library can integrate with it.

This is not a full charm — it has no charm code, no Pebble, no hooks. It
directly manages the relation databag and secrets in jjx state, mimicking the
output of the postgresql-k8s charm's provider side.
"""

from __future__ import annotations

import json
import secrets
import string
import subprocess
import time
from typing import Any

from . import _engine


POSTGRES_IMAGE = "postgres:16"
POSTGRES_PORT = 5432


def _generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_username() -> str:
    return f"jjx_user_{secrets.token_hex(4)}"


def _docker_exec(container_name: str, command: list[str], timeout: float = 30.0) -> str:
    """Run a command inside a container and return stdout."""
    cmd = ["docker", "exec", container_name, *command]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        raise _engine.CliError(
            f"docker exec failed in {container_name}: {exc.stderr.strip() or exc.stdout.strip()}"
        ) from None
    except subprocess.TimeoutExpired as exc:
        raise _engine.CliError(f"docker exec timed out in {container_name}: {exc}") from None
    return proc.stdout


def _wait_for_postgres(container_name: str, timeout: float = 60.0) -> None:
    """Wait until postgres is ready to accept connections."""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _docker_exec(
                container_name,
                ["pg_isready", "-U", "postgres"],
                timeout=5.0,
            )
            return
        except _engine.CliError as exc:
            last_error = str(exc)
            time.sleep(1.0)
    raise _engine.CliError(f"postgres did not become ready in {container_name}: {last_error}")


def start_postgres(
    model_name: str,
    app_name: str,
    database_name: str,
) -> dict[str, Any]:
    """Start a postgres container and return provider state.

    Returns a dict with keys: container_name, ip_address, host, port,
    username, password, database.
    """
    container_name = _engine._sanitize_container_name(f"{model_name}-postgres")

    # Remove any stale container with the same name.
    _engine._docker_rm(container_name)

    postgres_password = _generate_password()
    container_id = _engine._docker_run(
        POSTGRES_IMAGE,
        container_name,
        env={
            "POSTGRES_PASSWORD": postgres_password,
            "POSTGRES_DB": database_name,
        },
    )

    _wait_for_postgres(container_name)

    details = _engine._docker_container_details(container_name)
    if not details.running:
        raise _engine.CliError(f"postgres container {container_name} is not running")

    # Create a dedicated user for the application (not the superuser).
    # In PostgreSQL 15+, the public schema no longer grants CREATE by default,
    # so we must explicitly grant schema permissions for the app to create tables.
    username = _generate_username()
    password = _generate_password()
    _docker_exec(
        container_name,
        [
            "psql",
            "-U",
            "postgres",
            "-d",
            database_name,
            "-c",
            f"CREATE USER {username} WITH PASSWORD '{password}';",
            "-c",
            f"GRANT ALL PRIVILEGES ON DATABASE {database_name} TO {username};",
            "-c",
            f"GRANT ALL ON SCHEMA public TO {username};",
        ],
    )

    return {
        "container_name": container_name,
        "container_id": container_id,
        "ip_address": details.ip_address,
        "host": details.ip_address,
        "port": POSTGRES_PORT,
        "username": username,
        "password": password,
        "database": database_name,
    }


def stop_postgres(container_name: str) -> None:
    """Stop and remove a postgres container."""
    _engine._docker_rm(container_name)


def populate_relation(
    model_state: dict[str, Any],
    relation: dict[str, Any],
    provider_app: str,
    pg_info: dict[str, Any],
) -> None:
    """Write the provider-side relation data and create the secret.

    This mimics what the postgresql-k8s charm's DatabaseProvides would write:
    - ``endpoints``: normal databag field "host:port"
    - ``secret-user``: secret URI containing username + password
    - ``provided-secrets``: JSON list ["secret-user"]
    - ``data``: JSON snapshot for diff tracking
    """
    # Create the secret with username and password.
    secret_id = _engine._next_secret_id(model_state)
    secret = {
        "id": secret_id,
        "label": None,
        "owner": provider_app,
        "content": {
            "username": pg_info["username"],
            "password": pg_info["password"],
        },
        "revision": 1,
        "grants": [],
    }
    _engine._secrets(model_state).append(secret)

    # Grant the secret to the relation (so the requirer can read it).
    secret["grants"].append({"relation_id": relation["id"], "unit": None})

    # Write provider app databag.
    app_bucket = _engine._relation_data_bucket(relation, provider_app, None)
    endpoints = f"{pg_info['host']}:{pg_info['port']}"
    app_bucket["endpoints"] = endpoints
    app_bucket["secret-user"] = secret_id
    app_bucket["provided-secrets"] = json.dumps(["secret-user"])
    # The 'data' field is a snapshot used by the diff() function in
    # data_interfaces. On first integration it's empty, so everything
    # we write appears as "added" in the diff.
    app_bucket["data"] = json.dumps({})
