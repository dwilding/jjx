# jjx design

`jjx` exists for one job: run integration tests for a small Kubernetes charm from local source with real `ops` and real Pebble, without a Juju controller.

## the `jjx` command

The primary interface is the `jjx` command, run from the charm project directory:

```
jjx          # run tests, then wait (Ctrl-C to tear down)
jjx -d       # run tests, then detach (container stays up; use 'jjx down' to tear down)
jjx down     # tear down all models and remove runtime state
```

Optional flags:

- `-p HOST:CONTAINER` — publish a container port to `127.0.0.1:HOST`

`jjx` handles the full lifecycle: preflight cleanup of any stale state, creating and removing the placeholder charm artifact, invoking pytest, and tearing down on exit.

## customization

Additional pytest arguments can be appended to `jjx`'s built-in defaults in the charm project's `pyproject.toml`:

```toml
[tool.jjx]
pytest-extra-args = ["-v", "-k", "test_deploy"]
```

`jjx` always controls the test directory and teardown behavior; `pytest-extra-args` are appended after the built-in defaults. For full control, use `uv run` directly:

```
uv run --group integration --with jjx pytest <args>
```

## under the hood

`jjx` invokes pytest via:

```
uv run --group integration --with jjx pytest tests/integration --no-juju-teardown [<pytest-extra-args>]
```

Internally, `jjx` may substitute `--with jjx` with `--python <venv>` or `--with-editable <path>` depending on how it was invoked (e.g. running from inside the charm's venv or from a local checkout). The user-facing equivalent is always `--with jjx`.

The test fixture only needs a `.charm` file to exist; `jjx` creates one automatically before running pytest and removes it afterwards. `jjx` treats it as a deploy trigger and does not unpack it.

No project dependency changes are required. `jjx` injects itself and assumes the project already provides its normal test dependencies.

## scope

Supported:

- single application
- single unit (`app/0`)
- deploy via a `.charm` argument interpreted as a trigger to run local `./src`
- config updates and status reporting
- hook tools needed by the charm
- real Pebble in Docker
- relations with virtual charms (see below)

Not supported:

- peers or subordinates
- multi-unit behavior
- controller features beyond this test niche

If a charm needs any of the above, use real Juju.

## virtual charms

`jjx` can recognize certain well-known charm names as "virtual" charms. A virtual charm has no charm code — `jjx` manages its workload and relation data directly.

Currently supported:

- `postgresql-k8s` — starts a real PostgreSQL 16 container in Docker and provides the `postgresql_client` interface. When a charm integrates with it, `jjx` populates the relation databag and creates a Juju secret with the database credentials, mimicking what the real `postgresql-k8s` charm's `DatabaseProvides` would write. The charm under test sees real relation data and real secrets, and can connect to the running PostgreSQL instance.

Virtual charm containers are named `<model>-<app>-postgres` and are cleaned up on model teardown alongside workload containers. They are removed first, before workload containers.

## runtime model

`jjx` is a short-lived CLI. Each command starts, reads or updates state, performs work, and exits.

State is local to the project working directory.

Charm code is executed from `./src/` inside a persistent Docker container (the "charm runner") that shares the workload container's network namespace. The Python interpreter is bind-mounted from the host `uv` environment.

The `.charm` file passed to deploy is a trigger only. `jjx` does not inspect or extract it.

## filesystem contract

`jjx` writes project-local state to `./.jjx/`:

- `./.jjx/.gitignore`
- `./.jjx/state.json`
- `./.jjx/hook-tools/`
- `./.jjx/charm/` (staged runtime charm directory with `src/`, `lib/`, `metadata.yaml`, `config.yaml`, and `.unit-state.db`)
- `./.jjx/socket` (Pebble API Unix socket, bind-mounted into both the workload and charm runner containers)

`jjx` also caches the Pebble binary at `~/.cache/jjx/pebble-bin`, downloaded from canonical/pebble GitHub Releases on first use. This cache is shared across projects and persists across model teardowns to enable reuse across multiple deployments.

Notes on generated runtime files:

- Pebble runtime files are created inside the workload container under Pebble's default state path: `/var/lib/pebble/default`.
- `./.jjx/socket` is the Pebble API Unix socket. It is bind-mounted into the workload container (where Pebble creates it) and into the charm runner container (where charm code and hook tools connect to it).
- `JJX_STATE_DIR` is set to `/jjx` (the in-container mount point of `.jjx/`) in the charm runner environment. Hook tools call back into `jjx` to read and write state; this env var lets them locate state directly.
- `./.jjx/charm/.unit-state.db` is created by charm runtime state persistence (written by `ops` via `sqlite3` to `JUJU_CHARM_DIR/.unit-state.db`, which inside the charm runner is `/charm/.unit-state.db`).

When the model is torn down, jjx removes the entire `./.jjx/` directory. The `~/.cache/jjx/pebble-bin` cache is kept for reuse across subsequent deployments.

### charm runner container

The charm runner is a persistent Docker container that executes charm hooks.

**Image**: `ubuntu/dotnet-deps:8.0-24.04_stable` — a chiseled Ubuntu image containing only the runtime libraries (glibc, libssl, libz, ca-certs) needed to run Python. No shell, no coreutils, no Python, no Pebble — everything is bind-mounted from the host. Because there is no `/bin/sh`, hook tool scripts use a direct Python shebang (`#!/python/bin/python3.XX`) rather than `#!/bin/sh`.

**Naming**: `<model>-operator` (e.g. `jjx-default-operator`). Like the postgres container naming, this uses a fixed suffix with no app name.

**Network**: The charm runner uses `--network=container:<workload>`, sharing the workload container's network namespace. This means charm code that connects to loopback addresses (`127.0.0.1`, `localhost`, `::1`) reaches the workload container directly.

**Bind mounts** (all read-only unless noted):
- Host Python (from `uv`) → `/python` (ro)
- Host venv site-packages → `/venv` (ro)
- jjx package source → `/jjx-src` (ro) — the `jjx` package itself lives at `/jjx-src/src/jjx/`
- `.jjx/charm/` → `/charm` (rw)
- `.jjx/` → `/jjx` (rw)

**Environment**: The charm runner container is started with `PYTHONPATH` set to `/venv/lib/python3.XX/site-packages:/jjx-src/src:/charm/lib`. However, `docker exec` does not inherit environment variables from `docker run`, so `jjx` passes `PATH` and `PYTHONPATH` explicitly via `docker exec -e` for each hook execution. This ensures both the charm process and its hook tool subprocesses can find `jjx` and the hook tools.

**Pebble socket**: A symlink is created inside the charm runner at `/charm/containers/<workload>/pebble.socket` → `/jjx/socket`, so `ops` can find the Pebble socket at the path it expects. The symlink and parent directory are created via Python (`pathlib.Path.mkdir` + `os.symlink`) because the charm runner image has no `mkdir` or `ln` in PATH.

**Entrypoint**: The container runs `python -c 'import time; time.sleep(999999)'` — it stays alive doing nothing. Charm hooks are executed via `docker exec`.

**Teardown**: The charm runner is removed *last* during model teardown, after postgres and workload containers. This ensures it is available for any cleanup hooks that might need it.

## execution contract

Exact sequence:

1. user runs `uv run --group integration --with jjx pytest -v tests/integration`
2. `uv` prepares an environment with test dependencies and `jjx`
3. `pytest` (via `jubilant`) invokes `juju ...` commands
4. those commands execute `jjx` in that same `uv` environment
5. for hook events, `jjx` runs `docker exec` into the charm runner container
6. inside the charm runner, `jjx` runs `/charm/src/charm.py` using the bind-mounted Python
7. the Python interpreter is bind-mounted from the outer `uv` environment (no nested `uv run` per hook)
8. hook tools are Python scripts (with a `#!/python/bin/python3.XX` shebang) that `import jjx` directly — they run as subprocesses of the charm process, inheriting its `PATH` and `PYTHONPATH`
9. charm code interacts with hook tools and Pebble, then exits; `jjx` persists resulting state

Deploy flow:

1. ensure `./.jjx` exists and load state
2. stage runtime charm files in `./.jjx/charm/` (`src/`, `metadata.yaml`, `config.yaml`)
3. start workload container and Pebble on Docker bridge networking (no host networking)
   - if `JJX_DOCKER_PUBLISH` is set to `HOST_PORT:CONTAINER_PORT`, add Docker publish `127.0.0.1:HOST_PORT:CONTAINER_PORT`
4. start charm runner container (`--network=container:<workload>`, bind-mounts for Python, venv, jjx source, charm dir, state dir)
5. create Pebble socket symlink inside charm runner (via Python, not `mkdir`/`ln`)
6. wait for Pebble socket to be connectable inside charm runner
7. generate hook tool scripts in `./.jjx/hook-tools/` (Python scripts with `#!/python/bin/python3.XX` shebangs)
8. run charm hooks via `docker exec` into charm runner (passing `PATH` and `PYTHONPATH` via `docker exec -e`)
9. persist resulting app and unit status

Config flow:

1. update state
2. run `config-changed` hook
3. persist resulting status

Destroy flow:

1. stop and remove postgres containers (if any)
2. stop and remove workload containers
3. stop and remove charm runner containers (last)
4. remove `./.jjx/` directory

## behavior guarantees

- real `ops` framework (`ops` 3.x)
- real Pebble API surface through Unix socket
- hook tools invoked as subprocess executables
- synchronous event execution (no queue, no background agent)
- deterministic single-unit semantics
- charm code that connects to loopback addresses (`127.0.0.1`, `localhost`, `::1`) reaches the workload container without exposing container ports on the host (the charm runner shares the workload's network namespace)

## constraints

- requires Docker
- requires Docker socket access from the calling shell (for example via docker group membership)
- requires Linux (Unix sockets + Docker `--network=container:` assumptions)
- assumes charm source is present in `./src`
- assumes charm metadata files are present in project

State isolation rule:
- `./.jjx/state.json` is internal runtime state and not a supported charm interface.
- Charm code must not read or write `./.jjx/state.json`; runtime behavior must not depend on charm access to this file.

These constraints are deliberate. They keep the system small, predictable, and fast to debug.

### why not bubblewrap

An earlier design used `bubblewrap` (a Linux sandboxing tool) to provide filesystem isolation and fidelity for charm hook execution. We considered it because it bind-mounts the staged charm directory to `/charm`, matching real Juju's `JUJU_CHARM_DIR=/charm` convention, and gives the charm process a restricted filesystem view.

We rejected it in favor of the charm runner container for three reasons:

1. **Network fidelity**: `bubblewrap` is a filesystem sandbox, not a network proxy. It required a `sitecustomize.py` shim to rewrite outbound TCP connects from loopback addresses to the workload container's bridge IP. The charm runner's `--network=container:<workload>` shares the workload's network namespace directly, so loopback just works — no shim needed.
2. **Operational complexity**: `bubblewrap` requires AppArmor configuration on some systems (notably GitHub Actions runners) to avoid permission errors. A Docker container has no such host-level configuration burden.
3. **Consistency**: The charm runner is a Docker container, just like the workload and postgres containers. Using Docker for all containers simplifies the mental model and the teardown logic.

## design intent

This is not a fake Juju platform. It is a focused test adapter.

Every feature must justify itself against one question: does this help single-unit local charm integration tests run with high fidelity and low complexity?

If not, it does not belong.
