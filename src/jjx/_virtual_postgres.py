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
import re
import secrets
import string
import time
from typing import Any

from . import _engine

POSTGRES_IMAGE = "docker.io/library/postgres:16"
POSTGRES_PORT = 5432


def _generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_username() -> str:
    return f"jjx_user_{secrets.token_hex(4)}"


def _docker_exec(container_name: str, command: list[str], timeout: float = 30.0) -> str:
    """Run a command inside a container and return stdout."""
    return _engine._docker_exec(container_name, command, timeout=timeout, check=True).stdout


def _wait_for_postgres(container_name: str, timeout: float = 60.0) -> None:
    """Wait until postgres is fully ready to accept connections.

    The postgres Docker entrypoint starts a temporary server to perform
    initialisation (creating users, databases, etc.), then shuts it down
    and starts the real server. ``pg_isready`` can report "accepting
    connections" during the temporary-server phase, but ``psql`` will fail
    with "the database system is shutting down" if we try to use it then.
    To avoid this race, we wait until we can run a query against the
    ``postgres`` maintenance database (which always exists).
    """
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _docker_exec(
                container_name,
                ["psql", "-U", "postgres", "-d", "postgres", "-c", "SELECT 1"],
                timeout=5.0,
            )
            return
        except _engine.CliError as exc:
            last_error = str(exc)
            time.sleep(1.0)
    raise _engine.CliError(f"postgres did not become ready in {container_name}: {last_error}")


def start_postgres_wrapper(
    model_name: str,
    app_name: str,
) -> dict[str, Any]:
    """Wrapper matching the standard StartFunc signature.

    Starts the postgres container with the default ``postgres`` maintenance
    database. The per-relation database is created lazily by
    :func:`populate_relation`, using the name the requirer requests (or, as a
    fallback, the requirer's app name — matching ``paas_charm``'s convention of
    ``database_name=self.app.name``).
    """
    return start_postgres(model_name, app_name)


def start_postgres(
    model_name: str,
    app_name: str,
) -> dict[str, Any]:
    """Start a postgres container and return provider state.

    Returns a dict with keys: container_name, ip_address, host, port,
    username, password, database. The ``username`` and ``password`` here are
    the superuser credentials (used by ``_ensure_database``); the per-relation
    database + app-level user/password are created lazily by
    :func:`populate_relation`.
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
            # Don't pre-create any application database — the per-relation
            # database is created lazily by populate_relation using the name
            # the requirer requests.
            "POSTGRES_DB": "postgres",
        },
    )

    _wait_for_postgres(container_name)

    details = _engine._docker_container_details(container_name)
    if not details.running:
        raise _engine.CliError(f"postgres container {container_name} is not running")

    return {
        "container_name": container_name,
        "container_id": container_id,
        "ip_address": details.ip_address,
        "host": details.ip_address,
        "port": POSTGRES_PORT,
        "username": "postgres",
        "password": postgres_password,
        "database": "postgres",
    }


def _ensure_database(pg_info: dict[str, Any], database_name: str) -> tuple[str, str]:
    """Ensure ``database_name`` exists in the postgres container.

    Returns ``(username, password)`` for a dedicated app user with full access
    to the database. In PostgreSQL 15+ the public schema no longer grants
    CREATE by default, so we explicitly grant schema permissions for the app
    to create tables.

    The database name is validated against a safe charset (alphanumeric,
    underscore, hyphen) before being used in SQL, preventing injection from
    arbitrary relation data. Hyphens are allowed because Juju app names (used
    as the fallback database name) commonly contain them; the name is always
    quoted in SQL statements.
    Safe to call more than once: ``CREATE DATABASE`` is skipped if the database
    already exists (e.g. on re-populate after relation-created).
    """
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", database_name):
        raise _engine.CliError(f"invalid database name: {database_name!r}")

    container_name = pg_info["container_name"]
    username = _generate_username()
    password = _generate_password()

    # CREATE DATABASE doesn't support IF NOT EXISTS, so check first. The
    # postgres database always exists (it's the maintenance database).
    if database_name != "postgres":
        result = _docker_exec(
            container_name,
            [
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-tAc",
                f"SELECT 1 FROM pg_database WHERE datname = '{database_name}'",
            ],
        )
        if "1" not in result.strip():
            _docker_exec(
                container_name,
                [
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    f'CREATE DATABASE "{database_name}";',
                ],
            )
    _docker_exec(
        container_name,
        [
            "psql",
            "-U",
            "postgres",
            "-d",
            database_name,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"CREATE USER {username} WITH PASSWORD '{password}';",
            "-c",
            f'GRANT ALL PRIVILEGES ON DATABASE "{database_name}" TO {username};',
            "-c",
            f"GRANT ALL ON SCHEMA public TO {username};",
        ],
    )
    return username, password


def _ensure_database_with_user(
    pg_info: dict[str, Any],
    database_name: str,
    username: str,
    password: str,
) -> None:
    """Ensure ``database_name`` exists and grant ``username`` access to it.

    Used on re-populate when the database name may have changed (e.g. from the
    fallback app name to the name the requirer actually requests). The database
    is created if it doesn't exist, and the existing user is granted access.
    Does not create a new user — the credentials stay stable across re-populates
    so the charm doesn't see a secret content change.
    """
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", database_name):
        raise _engine.CliError(f"invalid database name: {database_name!r}")

    container_name = pg_info["container_name"]

    # CREATE DATABASE doesn't support IF NOT EXISTS, so check first. The
    # postgres database always exists (it's the maintenance database).
    if database_name != "postgres":
        result = _docker_exec(
            container_name,
            [
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-tAc",
                f"SELECT 1 FROM pg_database WHERE datname = '{database_name}'",
            ],
        )
        if "1" not in result.strip():
            _docker_exec(
                container_name,
                [
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    f'CREATE DATABASE "{database_name}";',
                ],
            )
    _docker_exec(
        container_name,
        [
            "psql",
            "-U",
            "postgres",
            "-d",
            database_name,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'GRANT ALL PRIVILEGES ON DATABASE "{database_name}" TO {username};',
            "-c",
            f"GRANT ALL ON SCHEMA public TO {username};",
        ],
    )


def populate_relation(
    model_state: dict[str, Any],
    relation: dict[str, Any],
    provider_app: str,
    pg_info: dict[str, Any],
) -> None:
    """Write the provider-side relation data and create the secret.

    This mimics what the postgresql-k8s charm's DatabaseProvides would write:
    - ``database``: the database name (written via set_database)
    - ``endpoints``: normal databag field "host:port"
    - ``secret-user``: secret URI containing username + password
    - ``provided-secrets``: JSON list ["secret-user"]
    - ``data``: JSON snapshot for diff tracking

    The database name is derived from the requirer's ``database`` app databag
    field (written by ``DatabaseRequires.set_database`` during
    ``relation-created``). If that field is not yet set (e.g. on the first
    populate call before the requirer has run), the requirer's app name is used
    as a fallback — matching ``paas_charm``'s convention of
    ``database_name=self.app.name``.

    This function is idempotent: if called again after the requirer has set its
    ``database`` field, it reuses the existing secret if one was already created
    for this relation, updating its content if the database name changed.
    """
    # Refresh the container IP in case it changed since deploy time.
    from . import _virtual_registry

    _virtual_registry.resolve_endpoint_url(pg_info, POSTGRES_PORT)

    # Find the requirer app (the non-virtual side of the relation).
    requirer_app = None
    for app_name in relation.get("endpoints", {}):
        if app_name != provider_app:
            requirer_app = app_name
            break
    if requirer_app is None:
        return

    # Determine the database name: prefer the requirer's ``database`` field
    # (written by DatabaseRequires.set_database during relation-created), fall
    # back to the requirer's app name (matching paas_charm's convention).
    requirer_data = relation.get("data", {}).get(requirer_app, {})
    requirer_app_bucket = requirer_data.get("app", {})
    database_name = requirer_app_bucket.get("database") or requirer_app

    # Create or reuse the secret for this relation. On the first call, create
    # the database + a dedicated user and store the credentials in a new secret.
    # On re-populate (after the requirer has set its ``database`` field), reuse
    # the existing secret's credentials so the charm doesn't see a credential
    # change — only the database name may differ. The new database is created
    # and the existing user is granted access to it.
    secret_id = _engine._next_secret_id(model_state)
    existing_secret = None
    for secret in _engine._secrets(model_state):
        grants = secret.get("grants", [])
        if any(g.get("relation_id") == relation["id"] for g in grants):
            existing_secret = secret
            secret_id = secret["id"]
            break

    if existing_secret is not None:
        # Reuse the existing credentials; just ensure the (possibly new)
        # database exists and the existing user can access it.
        username = existing_secret["content"]["username"]
        password = existing_secret["content"]["password"]
        _ensure_database_with_user(pg_info, database_name, username, password)
    else:
        # First call: create the database + a new dedicated user.
        username, password = _ensure_database(pg_info, database_name)
        secret = {
            "id": secret_id,
            "label": None,
            "owner": provider_app,
            "content": {
                "username": username,
                "password": password,
            },
            "revision": 1,
            "grants": [{"relation_id": relation["id"], "unit": None}],
        }
        _engine._secrets(model_state).append(secret)

    # Write provider app databag.
    app_bucket = _engine._relation_data_bucket(relation, provider_app, None)
    app_bucket["database"] = database_name
    endpoints = f"{pg_info['host']}:{pg_info['port']}"
    app_bucket["endpoints"] = endpoints
    app_bucket["secret-user"] = secret_id
    app_bucket["provided-secrets"] = json.dumps(["secret-user"])
    # The 'data' field is a snapshot used by the diff() function in
    # data_interfaces. On first integration it's empty, so everything
    # we write appears as "added" in the diff.
    app_bucket["data"] = json.dumps({})
