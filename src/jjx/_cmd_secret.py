"""Secret management commands.

Implements the ``juju`` CLI secret commands that jubilant calls during
integration tests: ``add-secret``, ``grant-secret``, ``update-secret``,
``secrets``, ``show-secret``, and ``remove-secret``.

User secrets (created via ``juju add-secret``) are owned by the model and
granted to charm applications via ``juju grant-secret``. App-owned secrets
are created by the charm via the ``secret-add`` hook tool.

Secret content revisions are tracked so that ``juju update-secret`` can
create a new revision and dispatch a ``secret-changed`` event to observing
charms.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import _engine


def _read_content_file(file_arg: str) -> dict[str, str]:
    """Read secret content from a YAML ``--file`` argument."""
    import yaml

    path = Path(file_arg)
    if not path.exists():
        raise _engine.CliError(f"secret content file not found: {file_arg}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise _engine.CliError(f"secret content file must be a mapping, got {type(data).__name__}")
    return {str(k): str(v) for k, v in data.items()}


def add_secret(args: list[str], model: str | None) -> int:
    """Execute the ``add-secret`` command.

    Usage: juju add-secret <name> [--file <path>] [--info <desc>]

    Creates a user secret owned by the model. The secret's user-facing name
    is recorded; the charm references it by URI (printed on stdout).
    """
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    name: str | None = None
    content: dict[str, str] = {}
    info: str | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--file" and i + 1 < len(args):
            content = _read_content_file(args[i + 1])
            i += 2
            continue
        if token.startswith("--file="):
            content = _read_content_file(token.split("=", 1)[1])
            i += 1
            continue
        if token == "--info" and i + 1 < len(args):
            info = args[i + 1]
            i += 2
            continue
        if token.startswith("--info="):
            info = token.split("=", 1)[1]
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if name is None:
            name = token
        i += 1

    if name is None:
        raise _engine.CliError("usage: juju add-secret <name> [--file <path>]")

    secret_id = _engine._next_secret_id(model_state)
    now = _engine._now_iso()
    secret: dict[str, Any] = {
        "id": secret_id,
        "label": None,
        "name": name,
        "owner": "model",
        "content": dict(content),
        "revisions": [dict(content)],
        "revision": 1,
        "grants": [],
        "rotate": None,
        "expire": None,
        "description": info,
        "observers": {},
        "created": now,
        "updated": now,
    }
    _engine._secrets(model_state).append(secret)
    _engine._save_state(state)
    # juju add-secret prints the secret URI (secret:<id>) on stdout.
    sys.stdout.write(f"{_engine._secret_uri_for_juju(secret)}\n")
    return 0


def grant_secret(args: list[str], model: str | None) -> int:
    """Execute the ``grant-secret`` command.

    Usage: juju grant-secret <identifier> <app>[,<app>...]

    Grants the named applications read access to the secret. The identifier
    may be the secret's user-facing name or its URI.
    """
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    identifier: str | None = None
    apps: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("-"):
            i += 1
            continue
        if identifier is None:
            identifier = token
        else:
            apps.extend(a.strip() for a in token.split(",") if a.strip())
        i += 1

    if identifier is None:
        raise _engine.CliError("usage: juju grant-secret <identifier> <app>")
    if not apps:
        raise _engine.CliError("juju grant-secret requires at least one application")

    secret = _find_secret_by_identifier(model_state, identifier)
    if secret is None:
        raise _engine.CliError(f"secret not found: {identifier}")

    grants = _engine._secret_grants(secret)
    for app in apps:
        if app not in model_state.get("apps", {}):
            raise _engine.CliError(f"application {app} not found")
        grant_entry = {"app": app, "relation_id": None, "unit": None}
        if grant_entry not in grants:
            grants.append(grant_entry)
    _engine._save_state(state)
    return 0


def update_secret(args: list[str], model: str | None) -> int:
    """Execute the ``update-secret`` command.

    Usage: juju update-secret <identifier> [--file <path>] [--info <desc>]
           [--name <name>] [--auto-prune]

    Creates a new revision of the secret's content and dispatches a
    ``secret-changed`` event to every observing application.
    """
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    identifier: str | None = None
    content: dict[str, str] | None = None
    info: str | None = None
    name: str | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--file" and i + 1 < len(args):
            content = _read_content_file(args[i + 1])
            i += 2
            continue
        if token.startswith("--file="):
            content = _read_content_file(token.split("=", 1)[1])
            i += 1
            continue
        if token == "--info" and i + 1 < len(args):
            info = args[i + 1]
            i += 2
            continue
        if token.startswith("--info="):
            info = token.split("=", 1)[1]
            i += 1
            continue
        if token == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
            continue
        if token.startswith("--name="):
            name = token.split("=", 1)[1]
            i += 1
            continue
        if token == "--auto-prune":
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if identifier is None:
            identifier = token
        i += 1

    if identifier is None:
        raise _engine.CliError("usage: juju update-secret <identifier> [--file <path>]")

    secret = _find_secret_by_identifier(model_state, identifier)
    if secret is None:
        raise _engine.CliError(f"secret not found: {identifier}")

    if content is not None:
        revisions = _engine._secret_revisions(secret)
        revisions.append(dict(content))
        secret["content"] = dict(content)
        secret["revision"] = int(secret.get("revision", 1)) + 1
    if info is not None:
        secret["description"] = info
    if name is not None:
        secret["name"] = name
    secret["updated"] = _engine._now_iso()
    _engine._save_state(state)

    # Dispatch secret-changed to every observing app that has been granted
    # access and has tracked a revision. The owner of an app secret is also
    # an observer of its own secret if it tracks it by label.
    observers = secret.get("observers", {})
    granted_apps: set[str] = set()
    for grant in _engine._secret_grants(secret):
        app_name = grant.get("app")
        if app_name:
            granted_apps.add(app_name)
    # The owner app observes its own secret too.
    owner = secret.get("owner")
    if owner and owner != "model":
        granted_apps.add(owner)

    for app in sorted(granted_apps):
        if app not in model_state.get("apps", {}):
            continue
        # Only dispatch to apps that are actually observing (have tracked a
        # revision) — matching real Juju, which only notifies observers.
        observer = observers.get(app)
        if observer is None and app != owner:
            continue
        _engine._run_secret_changed_event(model_name, app, secret)

    return 0


def secrets(args: list[str], model: str | None) -> int:
    """Execute the ``secrets`` command.

    Usage: juju secrets [--owner <app>] [--format json]

    Returns a JSON mapping of ``{uri: {info}}`` in the format jubilant expects.
    """
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    owner_filter: str | None = None
    as_json = False
    i = 0
    while i < len(args):
        if args[i] == "--owner" and i + 1 < len(args):
            owner_filter = args[i + 1]
            i += 2
            continue
        if args[i].startswith("--owner="):
            owner_filter = args[i].split("=", 1)[1]
            i += 1
            continue
        if args[i] == "--format" and i + 1 < len(args):
            as_json = args[i + 1] == "json"
            i += 2
            continue
        if args[i].startswith("--format="):
            as_json = args[i].split("=", 1)[1] == "json"
            i += 1
            continue
        i += 1

    result: dict[str, dict[str, Any]] = {}
    for secret in _engine._secrets(model_state):
        owner = secret.get("owner", "model")
        if owner_filter is not None and owner != owner_filter:
            continue
        uri = _engine._secret_uri_for_juju(secret)
        created = secret.get("created", _engine._now_iso())
        updated = secret.get("updated", created)
        info: dict[str, Any] = {
            "revision": _engine._secret_latest_revision(secret),
            "owner": owner,
            "created": created,
            "updated": updated,
            "expires": secret.get("expire"),
            "rotation": secret.get("rotate"),
            "name": secret.get("name"),
            "label": secret.get("label"),
            "description": secret.get("description"),
        }
        # jubilant parses 'rotates' with fromisoformat only if the key is
        # present, so omit it entirely when there is no rotation schedule.
        rotates = secret.get("rotates")
        if rotates is not None:
            info["rotates"] = rotates
        result[uri] = info

    if as_json:
        sys.stdout.write(json.dumps(result))
    else:
        for uri, info in result.items():
            sys.stdout.write(f"{uri}\t{info.get('label') or info.get('name') or ''}\n")
    return 0


def show_secret(args: list[str], model: str | None) -> int:
    """Execute the ``show-secret`` command.

    Usage: juju show-secret <identifier> [--format json] [--reveal] [--revision <n>]

    Returns secret metadata, optionally with revealed content.
    """
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    identifier: str | None = None
    reveal = False
    revision: int | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--format" and i + 1 < len(args):
            i += 2
            continue
        if token.startswith("--format="):
            i += 1
            continue
        if token == "--reveal":
            reveal = True
            i += 1
            continue
        if token == "--revision" and i + 1 < len(args):
            try:
                revision = int(args[i + 1])
            except ValueError:
                raise _engine.CliError(f"invalid --revision value: {args[i + 1]}") from None
            i += 2
            continue
        if token.startswith("--revision="):
            try:
                revision = int(token.split("=", 1)[1])
            except ValueError:
                raise _engine.CliError(f"invalid --revision value: {token}") from None
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if identifier is None:
            identifier = token
        i += 1

    if identifier is None:
        raise _engine.CliError("usage: juju show-secret <identifier>")

    secret = _find_secret_by_identifier(model_state, identifier)
    if secret is None:
        raise _engine.CliError(f"secret not found: {identifier}")

    uri = _engine._secret_uri_for_juju(secret)
    created = secret.get("created", _engine._now_iso())
    updated = secret.get("updated", created)
    info: dict[str, Any] = {
        "revision": _engine._secret_latest_revision(secret),
        "owner": secret.get("owner", "model"),
        "created": created,
        "updated": updated,
        "expires": secret.get("expire"),
        "rotation": secret.get("rotate"),
        "name": secret.get("name"),
        "label": secret.get("label"),
        "description": secret.get("description"),
    }
    rotates = secret.get("rotates")
    if rotates is not None:
        info["rotates"] = rotates
    if reveal:
        if revision is not None:
            content = _engine._secret_content_for_revision(secret, revision)
        else:
            content = dict(secret.get("content", {}))
        info["content"] = {"Data": content}
        info["checksum"] = ""

    sys.stdout.write(json.dumps({uri: info}))
    return 0


def remove_secret(args: list[str], model: str | None) -> int:
    """Execute the ``remove-secret`` command.

    Usage: juju remove-secret <identifier> [--revision <n>]
    """
    state = _engine._load_state()
    model_name = _engine._require_model_name(state, model)
    model_state = state["models"][model_name]

    identifier: str | None = None
    revision: int | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--revision" and i + 1 < len(args):
            try:
                revision = int(args[i + 1])
            except ValueError:
                raise _engine.CliError(f"invalid --revision value: {args[i + 1]}") from None
            i += 2
            continue
        if token.startswith("--revision="):
            try:
                revision = int(token.split("=", 1)[1])
            except ValueError:
                raise _engine.CliError(f"invalid --revision value: {token}") from None
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if identifier is None:
            identifier = token
        i += 1

    if identifier is None:
        raise _engine.CliError("usage: juju remove-secret <identifier>")

    secret = _find_secret_by_identifier(model_state, identifier)
    if secret is None:
        raise _engine.CliError(f"secret not found: {identifier}")

    if revision is not None:
        revisions = _engine._secret_revisions(secret)
        idx = revision - 1
        if 0 <= idx < len(revisions):
            del revisions[idx]
            if revisions:
                secret["content"] = revisions[-1]
                secret["revision"] = len(revisions)
            else:
                _engine._secrets(model_state).remove(secret)
    else:
        _engine._secrets(model_state).remove(secret)
    _engine._save_state(state)
    return 0


def _find_secret_by_identifier(
    model_state: dict[str, Any], identifier: str
) -> dict[str, Any] | None:
    """Find a secret by URI, user-facing name, or label."""
    secret = _engine._find_secret_by_id(model_state, identifier)
    if secret is not None:
        return secret
    # Match by user-facing name (user secrets) or owner label.
    for candidate in _engine._secrets(model_state):
        if candidate.get("name") == identifier:
            return candidate
        if candidate.get("label") == identifier:
            return candidate
    return None
