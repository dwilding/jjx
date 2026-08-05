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

Extra pytest arguments can also be passed on the command line after `--`:

```
jjx -d -- -vv -k test_deploy
```

Arguments are assembled in this order:

1. `jjx`'s built-in defaults (`tests/integration --no-juju-teardown`)
2. `pytest-extra-args` from `pyproject.toml`
3. extra args from the command line (after `--`)

Command-line args come last so they take precedence over `pyproject.toml` — matching the convention that command-line options override configuration file defaults. `jjx` always controls the test directory and teardown behavior; both `pytest-extra-args` and CLI args are appended after the built-in defaults. For full control, use `uv run` directly (see launch modes below).

## under the hood

`jjx` invokes pytest via `uv run --group integration pytest tests/integration --no-juju-teardown [<pytest-extra-args>] [<cli-extra-args>]`. How `jjx` makes itself available to the inner `uv run` depends on how it was launched:

- **Charm venv** (user added `jjx` to their charm's dependencies and runs `uv run jjx`): `--python <venv>` — pin uv to the current interpreter so the charm's existing venv (which already has `jjx`) is reused.
- **Local checkout** (developer running `uvx --with-editable <repo> jjx`): `--with-editable <path>` — install the same source into the inner venv.
- **Tool install** (user ran `uv tool install jjx`): no injection — the `juju` shim is already on `PATH` with a hardcoded shebang pointing at the tool's Python, which has the correct `jjx`. The inner `uv run` only needs the charm's integration dependencies.

The test fixture only needs a `.charm` file to exist; `jjx` creates one automatically before running pytest and removes it afterwards. `jjx` treats it as a deploy trigger and does not unpack it.

No project dependency changes are required. `jjx` injects itself (or relies on the tool-installed `juju` shim) and assumes the project already provides its normal test dependencies.

## scope

Supported:

- single application
- single unit (`app/0`)
- deploy via a `.charm` argument interpreted as a trigger to run local `./src`
- config updates and status reporting
- hook tools needed by the charm
- real Pebble in Docker
- relations with virtual charms (see below)
- multiple models (e.g. a charm model and a COS model)
- cross-model relations via `juju offer` and `juju integrate <model>.<app>`
- virtual bundles (e.g. `juju deploy cos-lite`)
- `juju run` (actions) on virtual charms

Not supported:

- peers or subordinates
- multi-unit behavior
- controller features beyond this test niche

If a charm needs any of the above, use real Juju.

## virtual charms

`jjx` can recognize certain well-known charm names as "virtual" charms. A virtual charm has no charm code — `jjx` manages its workload and relation data directly.

All virtual charms are registered in a central registry (`_virtual_registry.py`) that specifies their start function, relation populate function, endpoint metadata, display name, and teardown priority. Adding a new virtual charm requires only one registration call — no other file needs to change.

Currently supported:

- `postgresql-k8s` — starts a real PostgreSQL 16 container and provides the `postgresql_client` interface.
- `loki-k8s` — starts a real Loki container and provides the `loki_push_api` interface. Workload logs flow via real Pebble log-targets.
- `prometheus-k8s` — starts a real Prometheus container (with `--web.enable-lifecycle` for config reloads) and consumes the `prometheus_scrape` interface. Configures itself from the charm's relation data.
- `grafana-k8s` — starts a real Grafana container and consumes the `grafana_dashboard` interface. Provisions Prometheus and Loki as datasources, imports dashboards from relation data.
- `traefik-k8s` — a state-only virtual charm (no container) that responds to the `show-proxied-endpoints` action with the URLs of other COS charms.

Virtual bundles (e.g. `cos-lite`) deploy multiple virtual charms in one `juju deploy` command.

Virtual charm containers are named `<model>-<app>` and are cleaned up on model teardown. Teardown order is determined by each charm's `teardown_priority`: COS containers (grafana=10, prometheus=20, loki=30) are removed first, then postgres (40), workload (50), and charm runners (60).

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
- `./.jjx/<app>.<pid>.deploy` (marker files for in-flight background pebble-ready processes; created by deploy, deleted by the process on completion or by teardown)
- `./.jjx/prom-config-<app>/` (Prometheus config directory, bind-mounted into the Prometheus container)
- `./.jjx/grafana-config-<app>/` (Grafana provisioning directory, bind-mounted into the Grafana container)

`jjx` also caches the Pebble binary at `~/.cache/jjx/pebble-bin`, downloaded from canonical/pebble GitHub Releases on first use. This cache is shared across projects and persists across model teardowns to enable reuse across multiple deployments.

Notes on generated runtime files:

- Pebble runtime files are created inside the workload container under a jjx-managed state path: `./.jjx/pebble/` (mounted at `/jjx/pebble` in the container). Any baked-in Pebble layers from the OCI image (e.g. Rockcraft layers at `/var/lib/pebble/default/layers/`) are copied into this directory before Pebble starts, so service definitions like `startup: enabled` are preserved for charm layers using `override: merge`.
- `./.jjx/socket` is the Pebble API Unix socket. It is bind-mounted into the workload container (where Pebble creates it) and into the charm runner container (where charm code and hook tools connect to it).
- `JJX_STATE_DIR` is set to `/jjx` (the in-container mount point of `.jjx/`) in the charm runner environment. Hook tools call back into `jjx` to read and write state; this env var lets them locate state directly.
- `./.jjx/charm/.unit-state.db` is created by charm runtime state persistence (written by `ops` via `sqlite3` to `JUJU_CHARM_DIR/.unit-state.db`, which inside the charm runner is `/charm/.unit-state.db`).

When a model is torn down, jjx kills any background pebble-ready processes for that model's apps (via `.deploy` marker files), removes its containers in priority order, and removes the model from state. When the last model is torn down, the entire `./.jjx/` directory is removed. The `~/.cache/jjx/pebble-bin` cache is kept for reuse across subsequent deployments.

### charm runner container

The charm runner is a persistent Docker container that executes charm hooks.

**Image**: `docker.io/ubuntu/dotnet-deps:8.0-24.04_stable` — a chiseled Ubuntu image containing only the runtime libraries (glibc, libssl, libz, ca-certs) needed to run Python. No shell, no coreutils, no Python, no Pebble — everything is bind-mounted from the host. Because there is no `/bin/sh`, hook tool scripts use a direct Python shebang (`#!/python/bin/python3.XX`) rather than `#!/bin/sh`.

**Naming**: `<model>-operator` (e.g. `jjx-default-operator`). Like the postgres container naming, this uses a fixed suffix with no app name.

**Network**: The charm runner uses `--network=container:<workload>`, sharing the workload container's network namespace. This means charm code that connects to loopback addresses (`127.0.0.1`, `localhost`, `::1`) reaches the workload container directly.

**Bind mounts** (all read-only unless noted):
- Host Python (from `uv`) → `/python` (ro)
- Host venv site-packages → `/venv` (ro)
- jjx package source → `/jjx-src` (ro) — the parent of the `jjx` package directory, so `import jjx` works for both dev checkouts (`<repo>/src`) and installed tools (`site-packages`)
- `.jjx/charm/` → `/charm` (rw)
- `.jjx/` → `/jjx` (rw)

**Environment**: The charm runner container is started with `PYTHONPATH` set to `/venv/lib/python3.XX/site-packages:/jjx-src:/charm/lib`. However, `docker exec` does not inherit environment variables from `docker run`, so `jjx` passes `PATH` and `PYTHONPATH` explicitly via `docker exec -e` for each hook execution. This ensures both the charm process and its hook tool subprocesses can find `jjx` and the hook tools.

**Pebble socket**: A symlink is created inside the charm runner at `/charm/containers/<workload>/pebble.socket` → `/jjx/socket`, so `ops` can find the Pebble socket at the path it expects. The symlink and parent directory are created via Python (`pathlib.Path.mkdir` + `os.symlink`) because the charm runner image has no `mkdir` or `ln` in PATH. The symlink is **not** created at container startup or during `config-changed` — it is created by the background pebble-ready subprocess, after the minimum deploy delay (see "async pebble-ready" below). This models real Juju, where `config-changed` fires before the workload container has started, so the charm cannot reach Pebble yet. The charm's `_replan_workload` hits a `ConnectionError` and returns early, just like it does in real Juju.

**Entrypoint**: The container runs `python -c 'import time; time.sleep(999999)'` — it stays alive doing nothing. Charm hooks are executed via `docker exec`.

**Teardown**: The charm runner is removed *last* during model teardown (priority 60), after COS containers, postgres, and workload containers. This ensures it is available for any cleanup hooks that might need it.

## execution contract

Exact sequence:

1. user runs `jjx` (or `uv run jjx` if jjx is a charm dependency)
2. `jjx` invokes `uv run --group integration pytest tests/integration --no-juju-teardown` (with launch-mode-appropriate args to make `juju` available — see "under the hood")
3. `uv` prepares an environment with the charm's test dependencies
4. `pytest` (via `jubilant`) invokes `juju ...` commands
5. those commands execute `jjx`'s `juju` shim (from the charm venv, the inner venv, or the tool install, depending on launch mode)
6. for hook events, `jjx` runs `docker exec` into the charm runner container
7. inside the charm runner, `jjx` runs `/charm/src/charm.py` using the bind-mounted Python
8. the Python interpreter is bind-mounted from the outer `uv` environment (no nested `uv run` per hook)
9. hook tools are Python scripts (with a `#!/python/bin/python3.XX` shebang) that `import jjx` directly — they run as subprocesses of the charm process, inheriting its `PATH` and `PYTHONPATH`
10. charm code interacts with hook tools and Pebble, then exits; `jjx` persists resulting state

Deploy flow:

1. ensure `./.jjx` exists and load state
2. stage runtime charm files in `./.jjx/charm/` (`src/`, `metadata.yaml`, `config.yaml`)
3. start workload container and Pebble with explicit `--network bridge` (no host networking)
   - Pebble is started with `run --hold --create-dirs` so baked-in layers (e.g. Rockcraft layers with `startup: enabled`) do not autostart services before the charm's pebble-ready hook fires — matching real Juju, which also uses `--hold`
   - if `JJX_PUBLISH` is set to `HOST_PORT:CONTAINER_PORT`, add port publish `127.0.0.1:HOST_PORT:CONTAINER_PORT`
   - the workload container's IP is stored in `state.json` as `container_ip` so hook tools (e.g. `network-get`) can access it without calling Docker directly
4. start charm runner container (`--network=container:<workload>`, bind-mounts for Python, venv, jjx source, charm dir, state dir)
5. wait for Pebble socket (`/jjx/socket`) to be connectable inside charm runner
6. generate hook tool scripts in `./.jjx/hook-tools/` (Python scripts with `#!/python/bin/python3.XX` shebangs)
7. run `config-changed` hook via `docker exec` into charm runner (Pebble socket symlink does **not** exist yet — charm cannot reach Pebble, matching real Juju)
8. persist resulting app and unit status
9. spawn a detached background subprocess (see "async pebble-ready" below) and return

The background subprocess:

1. sleep for 3.0 seconds (the minimum deploy delay)
2. create Pebble socket symlink inside charm runner (via Python, not `mkdir`/`ln`) — Pebble is now accessible from the charm
3. run `<workload>-pebble-ready` hook via `docker exec` into charm runner
4. persist resulting app and unit status
5. delete the `.jjx/<app>.<pid>.deploy` marker file and exit

### async pebble-ready

`pebble-ready` is dispatched asynchronously after a minimum 3.0-second delay.
This is a deliberate stress-test floor, not a model of real Juju latency. It
exposes integration tests that use `jubilant.wait` (which needs 3 consecutive
successful status checks at 1.0s intervals — minimum ~2.0s) and then proceed
to interact with the container without verifying that `pebble-ready` has
actually fired. The delay is not configurable; the floor must be guaranteed.

The background subprocess is tracked via a `.jjx/<app>.<pid>.deploy` marker
file (not in `state.json`) so teardown can find and kill it. State writes are
atomic (temp file + `os.replace`) to prevent torn JSON if a `juju status` read
overlaps a background write. No file locking — a flock around event dispatch
would deadlock on hook tool subprocesses.

Config flow:

1. update state
2. run `config-changed` hook
3. persist resulting status

Integrate flow:

1. match endpoints by interface
2. create relation in state
3. populate the remote (virtual) app's databag from the virtual provider
4. fire `relation-created`, `relation-joined`, then `relation-changed` on the local (real) charm
5. re-populate the remote (virtual) app's databag — the charm may have written data (e.g. `scrape_jobs`, `dashboards`) during `relation-joined`/`relation-changed` that the virtual charm needs to read

Destroy flow:

1. kill any background pebble-ready processes (via `.jjx/*.deploy` marker files)
2. remove containers in teardown-priority order: COS containers (grafana, prometheus, loki) → postgres → workload → charm runners
3. clean up any stragglers (orphaned containers not tracked in state)
4. remove this model from state; if it's the last model, remove `./.jjx/` entirely

When `jjx down` tears down all models, models are destroyed in reverse creation order so that COS models (created later) are torn down first.

## behavior guarantees

- real `ops` framework (`ops` 3.x)
- real Pebble API surface through Unix socket
- hook tools invoked as subprocess executables
- synchronous event execution (no queue, no background agent), except `pebble-ready` which is dispatched asynchronously after a minimum 3.0s delay (see "async pebble-ready" above)
- deterministic single-unit semantics
- charm code that connects to loopback addresses (`127.0.0.1`, `localhost`, `::1`) reaches the workload container without exposing container ports on the host (the charm runner shares the workload's network namespace)

## additional juju commands

jjx implements several juju commands that jubilant/pytest-jubilant may call during setup, teardown, or status checks. These are minimal stubs that return just enough data for jubilant to function:

- `juju offer` — records a cross-model offer in model state
- `juju run` — executes actions on virtual charms (e.g. traefik's `show-proxied-endpoints`)
- `juju switch` — no-op (jjx always uses `--model`)
- `juju version` — returns a minimal version response
- `juju show-model` — returns model metadata
- `juju models` — lists all models in state

## hook tools

jjx implements the hook tools that `ops` calls as subprocesses. Each is a Python script in `./.jjx/hook-tools/` with a `#!/python/bin/python3.XX` shebang that `import jjx` directly.

Implemented:

- `config-get`, `status-get`, `status-set` — config and status management
- `is-leader` — always returns `true` (single-unit model)
- `juju-log` — appends to the model's log in `state.json`
- `relation-ids`, `relation-list`, `relation-get`, `relation-set`, `relation-model-get` — relation data access
- `secret-add`, `secret-get`, `secret-grant`, `secret-info-get`, `secret-ids`, `secret-remove`, `secret-revoke`, `secret-set` — secret management
- `network-get` — returns the workload container's IP address (from `state.json`, not Docker, since Docker isn't available inside the charm runner). All bindings resolve to the workload's IP.
- `application-version-set` — sets the workload version in state

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
