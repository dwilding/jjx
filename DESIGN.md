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

`jjx` handles the full lifecycle: preflight cleanup of any stale state, a container runtime availability check, creating and removing the placeholder charm artifact, invoking pytest, and tearing down on exit. After pytest finishes, `jjx` checks whether the charm's workload container is running (virtual charm containers don't count). If it is, `jjx` waits for Ctrl-C (or detaches with `-d`); if not, `jjx` tears down all models and exits.

jjx's own errors and warnings share one format: an `Error: ` or `Warning: ` prefix, a capitalized first word unless that word is a proper name (`charm-user`, `pytest-jubilant`), and a trailing period. The `juju` shim's messages are outside this rule. They follow real Juju's shapes, because jubilant carries their stderr verbatim into the exceptions it raises.

## customization

Additional pytest arguments can be appended to `jjx`'s built-in defaults in the charm project's `pyproject.toml`:

```toml
[tool.jjx]
pytest-extra-args = ["-k", "test_deploy"]
```

Extra pytest arguments can also be passed on the command line after `--`:

```
jjx -d -- -k test_deploy
```

Arguments are assembled in this order:

1. `jjx`'s built-in defaults (`tests/integration --no-juju-teardown -v`)
2. `pytest-extra-args` from `pyproject.toml`
3. extra args from the command line (after `--`)

Command-line args come last so they take precedence over `pyproject.toml` — matching the convention that command-line options override configuration file defaults. `jjx` always controls the test directory and teardown behavior; both `pytest-extra-args` and CLI args are appended after the built-in defaults. For full control, use `uv run` directly (see launch modes below).

## under the hood

`jjx` invokes pytest via `uv run --group integration pytest tests/integration --no-juju-teardown -v [<pytest-extra-args>] [<cli-extra-args>]`. How `jjx` makes itself available to the inner `uv run` depends on how it was launched:

- **Charm venv** (user added `jjx` to their charm's dependencies and runs `uv run jjx`): `--python <venv>` — pin uv to the current interpreter so the charm's existing venv (which already has `jjx`) is reused. This mode requires the charm's `requires-python` to be within jjx's range: uv resolves across the charm's full `requires-python` range.
- **Local checkout** (developer running `uvx --with-editable <repo> jjx`): `--with-editable <path>` — install the same source into the inner venv.
- **Tool install** (user ran `uv tool install jjx`): no injection — the `juju` shim is already on `PATH` with a hardcoded shebang pointing at the tool's Python, which has the correct `jjx`. The inner `uv run` only needs the charm's integration dependencies.

The test fixture only needs a `.charm` file to exist; `jjx` creates one automatically before running pytest and removes it afterwards. `jjx` treats it as a deploy trigger and does not unpack it.

No project dependency changes are required. `jjx` injects itself (or relies on the tool-installed `juju` shim) and assumes the project already provides its normal test dependencies.

## interrupt choreography

The pytest tree runs in its own session, so a terminal Ctrl-C reaches `jjx` alone:

1. `jjx` records the press without raising. Launchers such as uvx relay a duplicate back within milliseconds. Only a press at least a quarter-second later counts as a second one.
2. `jjx` SIGKILLs the `uv` wrapper, which would otherwise forward a second interrupt to pytest and never exit on its own.
3. `jjx` relays one SIGINT to the tree and waits up to 60 seconds for it to exit. pytest prints its report and runs fixture teardown, which for a failed session includes pytest-jubilant's model debug-log dump.
4. Only after the tree has exited does `jjx` destroy the models, so the debug-log dump always finds them. A blank line separates the teardown output from the test report. It exits 130, the standard Ctrl-C code.

A second Ctrl-C, or closing the terminal, SIGKILLs the tree immediately instead of waiting. A relayed interrupt also reaches any in-flight `juju` shim call, which exits 130 rather than printing a traceback.

## scope

Supported:

- single application
- single unit (`app/0`)
- Juju 4.x only — jjx reports a pinned 4.x version (see `juju version` below) and emits Juju 4 JSON field names. It is not tested against Juju 3.x and does not try to be cross-version.
- deploy via a `.charm` argument interpreted as a trigger to run local `./src`
- config updates and status reporting
- hook tools needed by the charm
- real Pebble in a container (the real binary, running as the workload's service manager)
- relations with virtual charms (see below)
- multiple models (e.g. a charm model and a COS model)
- cross-model relations via `juju offer` and `juju integrate <model>.<app>`
- virtual bundles (e.g. `juju deploy cos-lite`)
- `juju run` (actions) on real and virtual charms
- app-managed and user secrets, including `secret-changed` event dispatch

Not supported:

- peers or subordinates
- multi-unit behavior
- controller features beyond this test niche

If a charm needs any of the above, use real Juju.

## virtual charms

`jjx` can recognize certain well-known charm names as "virtual" charms. A virtual charm has no charm code — `jjx` manages its workload and relation data directly.

All virtual charms are registered in a central registry (`_virtual_registry.py`) that specifies their start function, relation populate function, endpoint metadata, display name, and teardown priority. Adding a new virtual charm requires only one registration call — no other file needs to change.

Currently supported:

- `postgresql-k8s` — starts a real PostgreSQL 16 container and provides the `postgresql_client` interface. The database is created lazily at integrate time, using the database name the requirer requests (falling back to the requirer's app name), so charms that request a name other than the default receive credentials for the right database.
- `loki-k8s` — starts a real Loki container and provides the `loki_push_api` interface. Workload logs flow via real Pebble log-targets.
- `prometheus-k8s` — starts a real Prometheus container (with `--web.enable-lifecycle` for config reloads) and consumes the `prometheus_scrape` interface. Configures itself from the charm's relation data.
- `grafana-k8s` — starts a real Grafana container and consumes the `grafana_dashboard` interface. Provisions Prometheus and Loki as datasources (via file-based provisioning, applied with a container restart since Grafana does not re-provision datasources on SIGHUP), imports dashboards from relation data, and injects datasource variables (`${prometheusds}`, `${lokids}`) into imported dashboards so Grafana can resolve the placeholder UIDs — mirroring what the real grafana-k8s charm does.
- `traefik-k8s` — a state-only virtual charm (no container) that responds to the `show-proxied-endpoints` action with the URLs of other COS charms.

Virtual bundles (e.g. `cos-lite`) deploy multiple virtual charms in one `juju deploy` command.

Virtual charm containers are named `<model>-<app>` and are cleaned up on model teardown. The real charm's workload container is named `<model>-<container>` (using the container name from `charmcraft.yaml`, not the app name). Teardown order is determined by each charm's `teardown_priority`: COS containers (grafana=10, prometheus=20, loki=30) are removed first, then postgres (40), then the charm runner (45) and its workload (50). The charm runner precedes the workload because podman refuses to remove a container that a dependent container still uses, and the runner shares the workload's network namespace.

## runtime model

`jjx` is a short-lived CLI. Each command starts, reads or updates state, performs work, and exits.

State is local to the project working directory.

Charm code is executed from `./src/` inside a persistent container (the "charm runner") that shares the workload container's network namespace. The Python interpreter is bind-mounted from the host `uv` environment.

The container runtime is Podman when `podman` is on PATH, else Docker. Set `JJX_RUNTIME` to `docker` or `podman` to pin the choice on a machine that has both; the preflight rejects any other value. jjx prefers Podman when both are installed: a machine with Podman chose rootless containers on purpose, while a machine with Docker often picked it up as a side effect of something else. jjx uses only CLI-compatible subcommands (`run`, `exec`, `inspect`, `ps`, `pull`, `rm`, `create`, `restart`), so the same code drives either. Docker-specific flags are avoided except where noted. The two behavioral switches are the shared-directory group (see "container users") and reachability: Docker and rootful Podman put a bridge interface on the host, so container IPs answer from the host; rootless Podman runs the network stack inside a user namespace with no host-side interface, so container IPs answer only from inside the container network.

The reachability gap shapes three decisions. First, virtual charm containers pass `network=bridge` explicitly: Docker defaults to the bridge network anyway, but rootless Podman defaults new containers to a private slirp network where no peer can reach them, and the bridge network keeps relation peers (workload to COS, workload to postgres) connected. Second, readiness polls on virtual charms come from a short-lived busybox container on the bridge network when the host cannot reach container IPs, not from the host. Third, the CLI prints a caveat when the address it reports is not host-reachable, and `--publish` is the supported way to expose a port on the host.

The `.charm` file passed to deploy is a trigger only. `jjx` does not inspect or extract it.

## container users

The charm runner always runs as `170:170`. jjx never runs charm code as root. What the charm declares determines whether jjx warns or refuses to run:

| `charm-user` declaration | Charm runner | jjx behavior |
|---|---|---|
| `non-root` | `170:170` | silent |
| unset | `170:170` | warning |
| `root` | — | jjx refuses to run |
| `sudoer` | — | jjx refuses to run |

Workload containers follow their own declarations, with no dependence on the charm-runner rules:

| Workload declaration (`uid:`/`gid:`) | Workload user |
|---|---|
| set | `--user <uid>:<gid>` |
| not set | no `--user`; the image's declared user applies, or root if the image declares none |

jjx follows any declared `uid`/`gid` value, with one guard: values in 1000–9999 are rejected at deploy, matching Juju's reserved range.

This diverges from real Juju, which runs charm code as root when `charm-user` is unset and forces the workload to root in that case too. The divergence is deliberate: it catches, before the charm declares anything, any dependence on the legacy behavior — a dependence that would break on real Juju the moment the charm sets `charm-user: non-root`. jjx shows the charm its destination, not its legacy.

Virtual charms are exempt: they have no charmcraft.yaml and no charm code, so the gate never runs.

The `jjx` CLI enforces the gate at preflight, before pytest starts: `root`/`sudoer` fail with a clean error, and unset prints the warning. Deploy re-checks as a backstop for direct shim use, but its message would otherwise be lost — under `jjx` the deploy subprocess runs inside the pytest tree, whose stderr capture turns a deploy-time failure into tracebacks from every test instead of one clean error.

Two mechanisms make the 170 runner work. First, Pebble's socket grants write access only to the daemon's own UID, root, or a seeded identity, so deploy passes `pebble run --identities` a file granting UID 170 admin access; without it the charm's Pebble writes are silently read-only. The file also grants the host user admin access. Host-side tools over `.jjx/socket` need it (borescope's `ps`, for example, uses the admin-only files API), and it grants nothing the host user doesn't already have: they create and destroy the containers and can `docker exec` into them, so the socket was never a boundary against them. Second, UID 170 owns nothing on the host, so the shared state directories under `.jjx/` are group-writable and both containers run with a supplemental group, letting the runner and the workload's Pebble daemon write files the host can still read and remove. Under Docker the supplemental group is the host GID; under rootless Podman the host GID is outside the container's gid map, so the containers add container group 0 instead, which maps to the host user's own group and yields the same host-owned outcome.

## filesystem contract

`jjx` writes project-local state to `./.jjx/`:

- `./.jjx/.gitignore`
- `./.jjx/state.json`
- `./.jjx/hook-tools/`
- `./.jjx/charm/` (staged runtime charm directory with `src/`, `lib/`, `metadata.yaml`, `config.yaml`, and `.unit-state.db`)
- `./.jjx/socket` (Pebble API Unix socket, bind-mounted into both the workload and charm runner containers)
- `./.jjx/<app>.<pid>.deploy` (marker files for in-flight background pebble-ready processes; created by deploy, deleted by the process on completion or by teardown)
- `./.jjx/action-<uuid>.json` (transient per-action results file; created by `juju run`, read/written by the action hook tools, deleted after the hook exits)
- `./.jjx/prom-config-<app>/` (Prometheus config directory, bind-mounted into the Prometheus container)
- `./.jjx/grafana-config-<app>/` (Grafana provisioning directory, bind-mounted into the Grafana container)

`jjx` caches the Pebble binary at `~/.cache/jjx/pebble-bin-<version>`, where `<version>` is the Pebble release tag pinned in `jjx/_version.py`. The binary is downloaded from the canonical/pebble GitHub Release for that tag on first use. The cache is shared across projects and persists across model teardowns to enable reuse across multiple deployments. Bumping the pinned version causes a fresh download (the old versioned cache entry is left behind).

The pinned Pebble version is not the latest Pebble release. Juju builds the Pebble binary it injects into containers from the `github.com/canonical/pebble` pin in its `go.mod`, so jjx pins the same version Juju pins at the `JUJU_VERSION` release tag. Charms under test then see the same Pebble API surface as under real Juju. A scheduled workflow (`.github/workflows/latest-juju.yaml`, weekly) checks the Snap Store's `4/stable` channel for a newer Juju, derives the matching Pebble pin, and opens a PR if the Juju version has moved.

Notes on generated runtime files:

- Pebble runtime files are created inside the workload container under a jjx-managed state path: `./.jjx/pebble/` (mounted at `/jjx/pebble` in the container). Any baked-in Pebble layers from the OCI image (e.g. Rockcraft layers at `/var/lib/pebble/default/layers/`) are copied into this directory before Pebble starts, so service definitions like `startup: enabled` are preserved for charm layers using `override: merge`.
- `./.jjx/pebble/identities.yaml` is written by deploy and passed to `pebble run --identities` at workload startup. It grants admin access to UID 170 (the charm runner) and to the host user. The host identity keeps host-side tools over `.jjx/socket` working for any workload user: the files API, which tools like borescope use, is admin-only. (See "container users" above.)
- `./.jjx/socket` is the Pebble API Unix socket. It is bind-mounted into the workload container (where Pebble creates it) and into the charm runner container (where charm code and hook tools connect to it).
- `JJX_STATE_DIR` is set to `/jjx` (the in-container mount point of `.jjx/`) in the charm runner environment. Hook tools call back into `jjx` to read and write state; this env var lets them locate state directly.
- `./.jjx/charm/.unit-state.db` is created by charm runtime state persistence (written by `ops` via `sqlite3` to `JUJU_CHARM_DIR/.unit-state.db`, which inside the charm runner is `/charm/.unit-state.db`).

When a model is torn down, jjx kills any background pebble-ready processes for that model's apps (via `.deploy` marker files), removes its containers in priority order, and removes the model from state. When the last model is torn down, the entire `./.jjx/` directory is removed. The `~/.cache/jjx/pebble-bin-<version>` cache is kept for reuse across subsequent deployments.

### charm runner container

The charm runner is a persistent container that executes charm hooks.

**Image**: `docker.io/ubuntu/dotnet-deps:8.0-24.04_stable` — a chiseled Ubuntu image containing only the runtime libraries (glibc, libssl, libz, ca-certs) needed to run Python. No shell, no coreutils, no Python, no Pebble — everything is bind-mounted from the host. Because there is no `/bin/sh`, hook tool scripts use a direct Python shebang (`#!/python/bin/python3.XX`) rather than `#!/bin/sh`.

**Naming**: `<model>-operator` (e.g. `jjx-default-operator`). Like the postgres container naming, this uses a fixed suffix with no app name.

**User**: Always `170:170`. See "container users" above.

**Network**: The charm runner uses `--network=container:<workload>`, sharing the workload container's network namespace. This means charm code that connects to loopback addresses (`127.0.0.1`, `localhost`, `::1`) reaches the workload container directly.

**Bind mounts** (all read-only unless noted):
- Host Python (from `uv`) → `/python` (ro)
- Host venv site-packages → `/venv` (ro)
- jjx package source → `/jjx-src` (ro) — the parent of the `jjx` package directory, so `import jjx` works for both dev checkouts (`<repo>/src`) and installed tools (`site-packages`)
- `.jjx/charm/` → `/charm` (rw)
- `.jjx/` → `/jjx` (rw)

**Environment**: The charm runner container is started with `PYTHONPATH` set to `/venv/lib/python3.XX/site-packages:/jjx-src:/charm/lib`, along with `HOME=/tmp` (the chiseled image has no passwd entry for UID 170) and `PYTHONDONTWRITEBYTECODE=1` (170-owned bytecode under the bind-mounted `/charm` would break restaging). However, `docker exec` does not inherit environment variables from `docker run`, so `jjx` passes `PATH`, `PYTHONPATH`, `HOME` and `PYTHONDONTWRITEBYTECODE` explicitly via `docker exec -e` for each hook execution. This ensures both the charm process and its hook tool subprocesses can find `jjx` and the hook tools.

**Pebble socket**: A symlink is created inside the charm runner at `/charm/containers/<workload>/pebble.socket` → `/jjx/socket`, so `ops` can find the Pebble socket at the path it expects. Deploy pre-creates the parent directory host-side, so it stays owned (and removable) by the host; the background pebble-ready subprocess then creates the symlink via Python (`os.symlink`) because the charm runner image has no `mkdir` or `ln` in PATH. The symlink is **not** created at container startup or during `config-changed` — it is created by the background pebble-ready subprocess, after the minimum deploy delay (see "async pebble-ready" below). This models real Juju, where `config-changed` fires before the workload container has started, so the charm cannot reach Pebble yet. The charm's `_replan_workload` hits a `ConnectionError` and returns early, just like it does in real Juju.

**Entrypoint**: The container runs `python -c 'import time; time.sleep(999999)'` — it stays alive doing nothing. Charm hooks are executed via `docker exec`.

**Teardown**: The charm runner is removed *last* during model teardown (priority 60), after COS containers, postgres, and workload containers. This ensures it is available for any cleanup hooks that might need it.

## execution contract

Exact sequence:

1. user runs `jjx` (or `uv run jjx` if jjx is a charm dependency)
2. `jjx` invokes `uv run --group integration pytest tests/integration --no-juju-teardown -v` (with launch-mode-appropriate args to make `juju` available — see "under the hood")
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
2. stage runtime charm files in `./.jjx/charm/` (`src/`, `metadata.yaml`, `config.yaml`) — `charm-user` is staged into `metadata.yaml` (where ops reads it), defaulting to `non-root` when charmcraft.yaml leaves it unset
3. gate on `charm-user` from charmcraft.yaml (see "container users" above): the `jjx` CLI has already rejected `root`/`sudoer` at preflight; deploy re-checks as a backstop for direct shim use, and the unset warning has already printed at preflight too.
4. pre-pull the workload image with retry (see "image pull retries" below), then start workload container and Pebble with explicit `--network bridge` (no host networking)
   - Pebble is started with `run --hold --create-dirs --identities /jjx/pebble/identities.yaml` so baked-in layers (e.g. Rockcraft layers with `startup: enabled`) do not autostart services before the charm's pebble-ready hook fires — matching real Juju, which also uses `--hold`. The identities file grants UID 170 admin access (see "container users" above).
   - the workload container runs with `--user <uid>:<gid>` when the charm declares them, otherwise at the image's declared user, or root if the image declares none
   - if `JJX_PUBLISH` is set to `HOST_PORT:CONTAINER_PORT`, add port publish `127.0.0.1:HOST_PORT:CONTAINER_PORT`
   - the workload container's IP is stored in `state.json` as `container_ip` so hook tools (for example, `network-get`) can access it without calling the container runtime directly
5. start charm runner container (`--network=container:<workload>`, running as `170:170`, bind-mounts for Python, venv, jjx source, charm dir, state dir) — its image is pre-pulled the same way
6. wait for Pebble socket (`/jjx/socket`) to be connectable inside charm runner
7. generate hook tool scripts in `./.jjx/hook-tools/` (Python scripts with `#!/python/bin/python3.XX` shebangs)
8. run `config-changed` hook via `docker exec` into charm runner (Pebble socket symlink does **not** exist yet — charm cannot reach Pebble, matching real Juju)
9. persist resulting app and unit status
10. spawn a detached background subprocess (see "async pebble-ready" below) and return

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

### image pull retries

All container image pulls (charm runner, workload, and virtual charm images) go
through `_docker_pull_with_retry` in `src/jjx/_engine.py`, which retries
image pulls up to 3 times with linear backoff (5s, then 10s) and skips images
already present locally. The Pebble binary download uses the same pattern. The
pull is always separated from the run: jjx pre-pulls, then creates the container
(which is not retried — retrying container creation would leave orphaned
containers).

### hook failures

When a hook exits non-zero, jjx sets unit and app status to `error` with the message `hook failed: "<hook-name>"`, matching real Juju's format. The actual exception traceback is in `juju debug-log` (ops logs it via `juju-log`). jjx does not retry failed hooks or implement `juju resolve` — the error status is sticky until the app is removed or a subsequent hook overwrites it.

For the background `pebble-ready` subprocess, the exception is caught (the process can't report to the test directly), but the error status has already been written to state, so `juju wait-for` fails fast instead of timing out.

Config flow:

1. update state
2. run `config-changed` hook
3. persist resulting status

Integrate flow:

1. match endpoints by interface
2. create relation in state
3. populate the remote (virtual) app's databag from the virtual provider, passing the model state the virtual app lives in (so the provider can find sibling virtual apps — e.g. grafana needs Prometheus and Loki, which live in the same COS model)
4. fire `relation-created`, `relation-joined`, then `relation-changed` on the local (real) charm
5. re-populate the remote (virtual) app's databag — the charm may have written data (e.g. `scrape_jobs`, `dashboards`) during `relation-joined`/`relation-changed` that the virtual charm needs to read

Destroy flow:

1. kill any background pebble-ready processes (via `.jjx/*.deploy` marker files)
2. remove containers in teardown-priority order: COS containers (grafana, prometheus, loki) → postgres → workload → charm runners
3. clean up any stragglers (orphaned containers not tracked in state)
   - this includes `jjx-layer-copy-*` throwaway containers created by deploy to extract baked-in Pebble layers from the OCI image; they are removed in a `finally` block when deploy completes, but a Ctrl-C mid-deploy can orphan them, so full teardown (`jjx down` or Ctrl-C) sweeps for them by name prefix
4. remove this model from state; if it's the last model, remove `./.jjx/` entirely

When `jjx down` tears down all models, models are destroyed in reverse creation order so that COS models (created later) are torn down first.

## behavior guarantees

- real `ops` framework (`ops` 3.x)
- real Pebble API surface through Unix socket
- hook tools invoked as subprocess executables
- synchronous event execution (no queue, no background agent), except `pebble-ready` which is dispatched asynchronously after a minimum 3.0s delay (see "async pebble-ready" above)
- hook failures set error status; no retry or `juju resolve` (see "hook failures" above)
- deterministic single-unit semantics
- charm code that connects to loopback addresses (`127.0.0.1`, `localhost`, `::1`) reaches the workload container without exposing container ports on the host (the charm runner shares the workload's network namespace)

## additional juju commands

jjx implements several juju commands that jubilant/pytest-jubilant may call during setup, teardown, or status checks. These are minimal stubs that return just enough data for jubilant to function:

- `juju offer` — records a cross-model offer in model state
- `juju run` — executes actions on real charms (dispatching the `actions/<name>` hook via `docker exec`) and on virtual charms (returning dynamically-computed results)
- `juju switch` — no-op (jjx always uses `--model`)
- `juju version` — returns a version response. The `version` string is in `major.minor.patch-release-arch` form (e.g. `4.0.14-jjx-amd64`), which is what jubilant's `Version._from_dict` parser requires; a bare `major.minor.patch` is rejected. The version jjx reports is pinned in `jjx/_version.py` (`JUJU_VERSION` for the bare form passed to charm code via `JUJU_VERSION`, `juju_version_string()` for the jubilant-facing form). The two formats differ because ops's `JujuVersion` parser (which reads `JUJU_VERSION`) rejects the `-release-arch` suffix.
- `juju show-model` — returns model metadata. Uses the Juju 4 field names (`short-name`, `model-uuid`, `model-type`, `controller-uuid`, `controller-name`, `is-controller`, `status`) that jubilant's `ModelInfo._from_dict` parser requires; the older 3.x names (`type`, `controller`, `uuid`, `model-status`) are no longer parsed.
- `juju models` — lists all models in state, using the same Juju 4 field names as `show-model`
- `juju add-secret` — creates a user secret owned by the model; prints the secret URI
- `juju grant-secret` — grants an application read access to a user secret
- `juju update-secret` — creates a new revision of a secret's content and dispatches `secret-changed` to every observing application
- `juju secrets` — lists secrets in the model (JSON format matching jubilant's `Secret` type)
- `juju show-secret` — returns secret metadata, optionally with revealed content
- `juju remove-secret` — removes a secret (or a single revision)

## hook tools

jjx implements the hook tools that `ops` calls as subprocesses. Each is a Python script in `./.jjx/hook-tools/` with a `#!/python/bin/python3.XX` shebang that `import jjx` directly.

Implemented:

- `config-get`, `status-get`, `status-set` — config and status management
- `is-leader` — always returns `true` (single-unit model)
- `juju-log` — appends to the model's log in `state.json`
- `relation-ids`, `relation-list`, `relation-get`, `relation-set`, `relation-model-get` — relation data access
- `secret-add`, `secret-get`, `secret-grant`, `secret-info-get`, `secret-ids`, `secret-remove`, `secret-revoke`, `secret-set` — secret management (see "secrets" below)
- `network-get` — returns the workload container's IP address (from `state.json`, not the container runtime, since the runtime isn't available inside the charm runner). All bindings resolve to the workload's IP.
- `application-version-set` — sets the workload version in state
- `action-get`, `action-set`, `action-fail`, `action-log` — action parameter access and result collection (see "actions" below)

## actions

`juju run <unit> <action>` dispatches the charm's `actions/<name>` hook via `docker exec` into the charm runner, with `JUJU_ACTION_NAME` and `JUJU_ACTION_UUID` set and `JUJU_HOOK_NAME` empty (per `ops`). The action hook tools communicate with `juju run` through a transient per-action results file (`./.jjx/action-<uuid>.json`): `action-get` reads the params, `action-set` accumulates results, `action-fail` sets the failure message, and `action-log` appends progress messages. After the hook exits, `juju run` reads the file and emits the task JSON jubilant expects.

A hook that exits non-zero (uncaught exception) marks the task `failed` and sets unit/app status to `error`, matching real Juju. A hook that calls `action-fail` marks the task `failed` without changing unit status. In both cases `juju run` exits non-zero with `task failed` on stderr (and the task JSON on stdout) so jubilant raises `TaskError`.

## secrets

jjx supports both app-managed secrets (created by the charm via `secret-add`) and user secrets (created via `juju add-secret` and granted to the charm via `juju grant-secret`).

Secrets are stored in `state.json` under `model_state["secrets"]`. Each secret tracks:

- `id` — the canonical URI (`secret://<model-uuid>/<id>`)
- `label` — the owner-assigned label (app secrets) or `null` (user secrets)
- `name` — the user-facing name (user secrets) or `null` (app secrets)
- `owner` — the owning application name, or `"model"` for user secrets
- `content` — the latest revision's content (a denormalized copy of `revisions[-1]`)
- `revisions` — a list of content dicts; index 0 is revision 1
- `revision` — the latest revision number
- `grants` — which applications have read access
- `rotate`, `expire`, `description` — optional metadata
- `created`, `updated` — ISO timestamps (for jubilant's `Secret.created`/`Secret.updated`)
- `observers` — per-app tracking: `{app: {label, tracked_revision}}`

When a charm calls `secret-get` with both an `id` and a `label`, jjx records the observer label so subsequent `secret-changed` events can supply it via `JUJU_SECRET_LABEL`. `secret-get` with `--refresh` or `--peek` returns the latest revision's content; `--refresh` also updates the observer's tracked revision. Without either flag, the tracked revision's content is returned (or the latest, if the observer hasn't tracked a revision yet).

`juju update-secret` creates a new revision and dispatches a `secret-changed` event to every granted application that is observing the secret. The event carries `JUJU_SECRET_ID` and the observer's `JUJU_SECRET_LABEL`; the charm re-reads the content with `get_content(refresh=True)`. The `secret-set` hook tool creates a new revision but does not dispatch `secret-changed` — it is called by the secret owner mid-hook (e.g. during `secret-rotate`), and jjx is single-app so there are no other observers to notify.

`secret-info-get` returns a single-entry dict keyed by the secret URI (`{uri: {revision, label, ...}}`), matching the format `ops` parses.

## constraints

- requires a container runtime: Docker or Podman
- requires container socket access from the calling shell (for example via docker group membership, or rootless Podman in the user's own session)
- requires Linux (Unix sockets + runtime `--network=container:` assumptions)
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
2. **Operational complexity**: `bubblewrap` requires AppArmor configuration on some systems (notably GitHub Actions runners) to avoid permission errors. A container has no such host-level configuration burden.
3. **Consistency**: The charm runner is a container, just like the workload and postgres containers. Using the container runtime for all containers simplifies the mental model and the teardown logic.

## design intent

This is not a fake Juju platform. It is a focused test adapter.

Every feature must justify itself against one question: does this help single-unit local charm integration tests run with high fidelity and low complexity?

If not, it does not belong.
